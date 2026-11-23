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
    for (const key of ["system", "messages", "tools", "options", "dom"]) {
      blocks.push(el("h3", { text: key }))
      blocks.push(el("pre", { class: "detail", text: JSON.stringify(snap[key] ?? null, null, 2) }))
    }
    view.replaceChildren(...blocks)
  } catch (error) {
    view.replaceChildren(el("p", { class: "status bad", text: String(error.message || error) }))
  }
}

async function renderClaims() {
  const query = encodeURIComponent($("claim-query").value.trim())
  const type = encodeURIComponent($("claim-type").value)
  const branch = encodeURIComponent($("claim-branch").value.trim())
  const body = await api(`/api/claims?query=${query}&type=${type}&branch=${branch}&limit=200`)
  const rows = (body.items || []).map((item) => [
    item.type,
    item.branch,
    item.is_current ? "current" : "retired",
    item.preview,
  ])
  $("claims-table").replaceChildren(table(["type", "branch", "state", "preview"], rows, async (_row, index) => {
    const item = body.items[index]
    if (!item) return
    try {
      showDetail($("claim-detail"), await api(`/api/claim/${item.claim_id}`))
    } catch (error) {
      showDetail($("claim-detail"), String(error.message || error))
    }
  }))
}

async function renderPlan() {
  showDetail($("plan-view"), await api("/api/plan"))
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
