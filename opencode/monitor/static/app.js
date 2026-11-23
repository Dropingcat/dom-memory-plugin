const $ = (id) => document.getElementById(id)
const state = { token: sessionStorage.getItem("dom-monitor-token") || "", tab: "hooks" }

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = String(value)
    else if (key === "class") node.className = String(value)
    else node.setAttribute(key, String(value))
  }
  for (const child of children) node.append(child)
  return node
}

function setStatus(text, kind = "") {
  const node = $("status")
  node.textContent = text
  node.className = `status ${kind}`.trim()
}

async function api(path) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${state.token}` } })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body?.error?.code || `HTTP ${response.status}`)
  return body
}

function table(headers, rows, onRow) {
  const head = el("tr", {}, headers.map((value) => el("th", { text: value })))
  const body = el("tbody", {}, rows.map((row, index) => {
    const tr = el("tr", {}, row.map((cell) => el("td", { text: cell })))
    if (onRow) tr.addEventListener("click", () => onRow(row, index))
    return tr
  }))
  return el("table", {}, [el("thead", {}, [head]), body])
}

function showDetail(node, value) {
  node.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2)
}

async function renderHooks() {
  const [hooks, snaps] = await Promise.all([api("/api/hooks"), api("/api/snapshots")])
  const rows = (hooks.items || []).slice().reverse().map((item) => [
    item.ts || "",
    item.hook || "",
    item.status || "",
    item.duration_ms ?? "",
    item.session_id || "",
    item.injected?.chars ?? "",
  ])
  const container = $("hooks-table")
  const reversed = (hooks.items || []).slice().reverse()
  container.replaceChildren(table(["time", "hook", "status", "ms", "session", "chars"], rows, (_row, index) => {
    showDetail($("hook-detail"), reversed[index] || {})
  }))
  const select = $("snapshot-select")
  select.replaceChildren()
  if (!snaps.items || snaps.items.length === 0) {
    select.append(el("option", { value: "", text: "no snapshots" }))
  } else {
    for (const item of snaps.items) select.append(el("option", { value: item.id, text: item.id }))
    select.addEventListener("change", () => loadSnapshot(select.value))
    loadSnapshot(snaps.items[0].id)
  }
}

async function loadSnapshot(id) {
  if (!id) return
  const view = $("snapshot-view")
  view.replaceChildren(el("p", { text: "loading…" }))
  try {
    const snap = await api(`/api/snapshot/${id}`)
    const blocks = []
    blocks.push(el("p", { text: `schema ${snap.schema || "?"} · truncated ${snap.truncated} · hook_order ${snap.hook_order || "?"}` }))
    if (Array.isArray(snap.system_blocks)) {
      blocks.push(el("h3", { text: "system blocks" }))
      blocks.push(table(["#", "origin", "chars", "head"], snap.system_blocks.map((block) => [
        block.index,
        block.origin,
        block.chars,
        block.head,
      ])))
    }
    if (Array.isArray(snap.dom_tools) && snap.dom_tools.length) {
      blocks.push(el("p", { text: `DOM tools in Code Mode: ${snap.dom_tools.join(", ")}` }))
    }
    for (const key of ["system", "messages", "tools", "options", "dom"]) {
      blocks.push(el("h3", { text: key }))
      blocks.push(el("pre", { class: "detail", text: JSON.stringify(snap[key] ?? null, null, 2) }))
    }
    view.replaceChildren(...blocks)
  } catch (error) {
    view.replaceChildren(el("p", { class: "status bad", text: String(error.message || error) }))
  }
}

function showClaimDetail(item) {
  const view = $("claim-detail")
  const meta = [
    ["claim", item.claim_id],
    ["logical", item.logical_claim_id],
    ["version", item.version],
    ["state", item.is_current ? "current" : "retired"],
    ["type", item.type],
    ["branch", item.branch],
    ["review", item.origin_version],
  ]
  const anatomy = item.anatomy || {}
  const anatomyRows = []
  if (anatomy.purpose) anatomyRows.push(["purpose", anatomy.purpose])
  if (anatomy.basis) anatomyRows.push(["basis", anatomy.basis.join(", ")])
  if (anatomy.usage) anatomyRows.push(["usage", anatomy.usage.join(", ")])
  if (anatomy.keywords) anatomyRows.push(["keywords", anatomy.keywords.join(", ")])
  if (anatomy.refines) anatomyRows.push(["refines", anatomy.refines])
  const properties = (item.properties || []).map((property) => [
    property.name,
    typeof property.value === "string" ? property.value : JSON.stringify(property.value),
    property.reason || "",
  ])
  const versions = (item.versions || []).map((version) => [
    version.version,
    version.claim_id ? version.claim_id.slice(0, 8) : "",
    version.is_current ? "yes" : "no",
    version.supersedes ? version.supersedes.slice(0, 8) : "",
  ])
  const raw = el("details", {}, [
    el("summary", { text: "raw JSON (hashes and internals)" }),
    el("pre", { class: "detail", text: JSON.stringify({ payload: item.payload, events: item.events, raw_sha256: item.raw_sha256 }, null, 2) }),
  ])
  view.replaceChildren(
    el("h3", { text: "text" }),
    el("pre", { class: "detail", text: item.text || "" }),
    el("h3", { text: "identity" }),
    table(["key", "value"], meta),
    el("h3", { text: "properties" }),
    properties.length ? table(["name", "value", "reason"], properties) : el("p", { text: "none" }),
    el("h3", { text: "anatomy" }),
    anatomyRows.length ? table(["key", "value"], anatomyRows) : el("p", { text: "none" }),
    el("h3", { text: "versions" }),
    versions.length ? table(["v", "claim", "current", "supersedes"], versions) : el("p", { text: "none" }),
    raw,
  )
}

async function renderClaims() {
  const query = encodeURIComponent($("claim-query").value.trim())
  const type = encodeURIComponent($("claim-type").value)
  const branch = encodeURIComponent($("claim-branch").value.trim())
  const body = await api(`/api/claims?query=${query}&type=${type}&branch=${branch}&limit=200`)
  const rows = (body.items || []).map((item) => [
    item.version,
    item.type,
    item.branch,
    item.is_current ? "current" : "retired",
    item.preview,
  ])
  $("claims-table").replaceChildren(table(["v", "type", "branch", "state", "preview"], rows, async (_row, index) => {
    const item = body.items[index]
    if (!item) return
    try {
      showClaimDetail(await api(`/api/claim/${item.claim_id}`))
    } catch (error) {
      showDetail($("claim-detail"), String(error.message || error))
    }
  }))
}

async function renderPlan() {
  const plan = await api("/api/plan/tree")
  const lines = []
  const walk = (tasks, depth = 0) => {
    for (const task of tasks || []) {
      lines.push(`${"  ".repeat(depth)}${task.path}  ${task.title}  [${task.branch || "-"}] [${task.status}] claims=${task.claim_count}`)
      walk(task.children, depth + 1)
    }
  }
  walk(plan.tasks)
  showDetail($("plan-view"), `revision ${plan.source_revision || "?"} · tasks ${plan.count || 0}\n\n${lines.join("\n")}`)
}

async function renderHealth() {
  const [health, hooks] = await Promise.all([api("/api/health"), api("/api/hooks")])
  const rows = [
    ["chain", health.chain_status],
    ["db", health.db_path],
    ["claims", health.counts?.claims ?? ""],
    ["events", health.events ?? ""],
    ["plan", health.plan_revision ?? ""],
    ["review", health.review_mode ?? ""],
    ["dropped events", hooks.dropped_events ?? 0],
    ["warnings", (health.warnings || []).join(", ")],
  ]
  $("health-view").replaceChildren(table(["key", "value"], rows))
  setStatus(health.chain_status === "CHAIN_OK" ? "chain OK" : "ATTENTION", health.chain_status === "CHAIN_OK" ? "ok" : "bad")
}

async function render() {
  if (!state.token) {
    setStatus("enter token", "bad")
    return
  }
  setStatus("loading…")
  try {
    if (state.tab === "hooks") await renderHooks()
    if (state.tab === "claims") await renderClaims()
    if (state.tab === "plan") await renderPlan()
    if (state.tab === "health") await renderHealth()
    setStatus("connected", "ok")
  } catch (error) {
    setStatus(String(error.message || error), "bad")
  }
}

function switchTab(tab) {
  state.tab = tab
  for (const button of document.querySelectorAll("[data-tab]")) button.classList.toggle("active", button.dataset.tab === tab)
  for (const section of document.querySelectorAll(".view")) section.classList.toggle("active", section.id === tab)
  render()
}

function connect() {
  state.token = $("token").value.trim()
  sessionStorage.setItem("dom-monitor-token", state.token)
  render()
}

window.addEventListener("DOMContentLoaded", () => {
  $("token").value = state.token
  $("connect").addEventListener("click", connect)
  $("claim-search").addEventListener("click", renderClaims)
  $("snapshot-refresh").addEventListener("click", renderHooks)
  for (const button of document.querySelectorAll("[data-tab]")) button.addEventListener("click", () => switchTab(button.dataset.tab))
  if (state.token) render()
  else setStatus("enter token", "bad")
})
