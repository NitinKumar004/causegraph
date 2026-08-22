/* CauseGraph — three-pane viewer. cytoscape lays out + draws connectors; every
   node is an HTML card. The engine does the reasoning; this only presents it. */
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
const base = (p) => (p || "").split("/").filter(Boolean).pop() || p || "?";

// ---- visual helpers ----
// Monochrome by design: tiles are neutral slate (no per-process hue), red is
// reserved for hot CPU (>=90%) and Kill only; everything else is slate.
const HOT = 90;
function abbrev(name) {
  const parts = name.replace(/\.[a-z0-9]+$/i, "").replace(/[^A-Za-z0-9]+/g, " ").trim().split(/(?<=[a-z])(?=[A-Z])|\s+/).filter(Boolean);
  return (parts.length >= 2 ? parts[0][0] + parts[1][0] : (name.replace(/[^A-Za-z0-9]/g, "") + "?").slice(0, 2)).toLowerCase();
}
function isHot(c) { return (c || 0) >= HOT; }
function cpuColor(c) { return isHot(c) ? "#ff7b72" : "#8a93a6"; }
function barFor(c) { return isHot(c) ? "linear-gradient(90deg,#f87171,#fb923c)" : "rgba(154,163,178,.45)"; }
function humanBytes(n) { if (n == null) return "—"; let v = n, i = 0; const u = ["B", "KiB", "MiB", "GiB", "TiB"]; while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; } return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`; }
function mib(n) { return n == null ? 0 : n / (1024 * 1024); }
function fmtTime(ns) { if (!ns) return "—"; const d = new Date(ns / 1e6); return d.toTimeString().slice(0, 8); }

// ---- state ----
let allProcs = [];       // every process node (for the list), from ?all=1
let graphData = null;    // current graph subset
let selectedId = null;
let sortMode = "cpu";
let filterCpu = 0, filterMem = 0, filterText = "";
const nodeById = {};     // id -> node meta from the current graph
let currentPid = null;   // the pid whose family the graph currently shows (for live refresh)
// auto-refresh: re-poll the DB on a timer, paused while the user is interacting so
// the view never yanks. autoMs = 0 means off. Cycled via the header "live" button.
const AUTO_STEPS = [4000, 8000, 15000, 0];  // 4s -> 8s -> 15s -> off -> (loops)
let autoMs = 4000, autoTimer = 0, refreshing = false, hoverActive = false, panActive = false;
// staleness: if the newest event ts stops advancing for 2 polls, the recorder has
// stopped writing — say "frozen" instead of pretending to be live.
let lastTs = null, staleCount = 0, frozen = false;

// ================= graph (cytoscape + HTML cards) =================
const cy = cytoscape({
  container: $("cy"), minZoom: 0.2, maxZoom: 1.5, wheelSensitivity: 0.22,
  style: [
    { selector: "node", style: { "width": "data(w)", "height": "data(h)", "shape": "round-rectangle", "background-opacity": 0, "border-width": 0, "events": "no" } },
    { selector: "edge", style: {
        "curve-style": "bezier", "width": 1.5, "line-color": "data(col)", "opacity": 0.9,
        "line-style": "dashed", "line-dash-pattern": [3, 10], "line-dash-offset": 0 } },
  ],
});
const layer = $("nodes");
let cards = {};

function nodeCardHTML(n) {
  const nm = base(n.kind === "process" ? n.exe : n.path);
  if (n.kind === "file") {
    return `<div class="r1"><span class="badge">${esc(abbrev(nm))}</span>
      <div style="flex:1;min-width:0"><div class="nm">${esc(nm)}</div><div class="sub">file</div></div></div>`;
  }
  const cpu = n.peak_cpu_pct;
  return `<div class="r1"><span class="badge">${esc(abbrev(nm))}</span>
      <span class="nm">${esc(nm)}</span><span class="sdot"></span>
      <span class="pc" style="color:${cpuColor(cpu)}">${cpu == null ? "—" : cpu.toFixed(1) + "%"}</span></div>
      <div class="pid">pid ${n.pid}</div>
      <div class="bar"><i style="width:${Math.max(3, Math.min(100, cpu || 0))}%;background:${barFor(cpu)}"></i></div>`;
}
function buildCards() {
  layer.innerHTML = ""; cards = {};
  cy.nodes().forEach((cyn) => {
    const n = cyn.data("meta");
    const div = document.createElement("div");
    div.className = "gnode" + (n.kind === "file" ? " file" : "") + (n.kind === "process" && isHot(n.peak_cpu_pct) ? " hot" : "");
    div.innerHTML = nodeCardHTML(n);
    div.addEventListener("click", (e) => { e.stopPropagation(); selectNode(n.id); });
    div.addEventListener("mouseenter", () => hoverNode(cyn));
    div.addEventListener("mouseleave", unhover);
    layer.appendChild(div); cards[n.id] = div;
  });
  positionCards(); applyGraphFilter();
}
function positionCards() {
  const z = cy.zoom();
  cy.nodes().forEach((cyn) => { const d = cards[cyn.id()]; if (!d) return; const p = cyn.renderedPosition(); d.style.transform = `translate3d(${p.x}px,${p.y}px,0) translate(-50%,-50%) scale(${z})`; });
}
// Sync on `render` (fires right after each canvas paint) so cards move in the SAME
// frame as the edges — pan/zoom events alone land on a different frame and the eye
// sees the cards lag the canvas while dragging.
cy.on("render pan zoom", positionCards); cy.on("position", "node", positionCards);
function syncFor(ms) { const end = performance.now() + ms; (function t() { positionCards(); if (performance.now() < end) requestAnimationFrame(t); })(); }
// Fit the graph but never below a legible zoom floor; center on the culprit when clamped.
function refit() {
  if (!cy.elements().nonempty()) return;
  cy.fit(cy.elements(), 80);
  const MIN = 0.82;
  if (cy.zoom() < MIN) {
    cy.zoom(MIN);
    const focus = graphData && cy.getElementById(graphData.culprit);
    if (focus && focus.nonempty()) cy.center(focus); else cy.center();
  }
  positionCards();
}

// marching-ants dashed edges (skip for very large graphs)
let dash = 0, animOn = false;
function animateEdges() {
  if (cy.edges().length > 140) { cy.edges().style("line-dash-offset", 0); animOn = false; return; }
  if (animOn) return; animOn = true;
  (function tick() { if (!animOn) return; dash = (dash - 0.7); cy.edges().style("line-dash-offset", dash); setTimeout(() => requestAnimationFrame(tick), 34); })();
}

function overlay(w) { for (const id of ["empty", "loading", "error"]) $(id).classList.toggle("show", id === w); }
function setError(m) { $("error-msg").textContent = m; overlay("error"); }

function renderGraph(data) {
  graphData = data; Object.keys(nodeById).forEach((k) => delete nodeById[k]);
  const els = [];
  for (const n of data.nodes) {
    nodeById[n.id] = n;
    const w = n.kind === "file" ? 190 : 208, h = n.kind === "file" ? 50 : 78;
    els.push({ data: { id: n.id, meta: n, w, h } });
  }
  for (const e of data.edges) els.push({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target, conf: e.conf, rule: e.rule, col: e.rule === "file_watch" ? "rgba(154,163,178,.6)" : "rgba(125,211,252,.6)" } });
  cy.elements().remove(); cy.add(els);
  const lay = cy.layout({ name: "breadthfirst", directed: true, padding: 40, spacingFactor: 1.2, avoidOverlap: true, animate: true, animationDuration: 380, animationEasing: "ease-out" });
  // Fit the whole graph on a legible zoom floor (see refit); a tall ancestry spine
  // would otherwise shrink 208px cards to unreadable ~70px. Overflow pans.
  lay.one("layoutstop", () => { refit(); syncFor(820); });
  lay.run(); buildCards(); syncFor(900); animateEdges(); overlay(null);
  $("hcount").textContent = ` · ${data.nodes.length} node${data.nodes.length === 1 ? "" : "s"}`;
  $("status").textContent = `observing · ${data.nodes.length} nodes · ${data.edges.length} edges` + (data.truncated ? " · truncated" : "");
}
function hoverNode(cyn) { hoverActive = true; const hi = cyn.closedNeighborhood(); const keep = new Set(hi.nodes().map((x) => x.id())); cy.edges().style("opacity", 0.08); hi.edges().style("opacity", 0.95); for (const id in cards) cards[id].classList.toggle("dim", !keep.has(id)); }
function unhover() { hoverActive = false; cy.edges().style("opacity", 0.9); for (const id in cards) cards[id].classList.remove("dim"); }
cy.on("tap", (e) => { if (e.target === cy) { /* keep selection */ } });

// A process passes the CPU/MEM/name filter — the one predicate both the list and
// the graph obey, so sliding a filter dims the same nodes in both places.
function matchesFilter(n) {
  if ((n.peak_cpu_pct || 0) < filterCpu || mib(n.peak_rss_bytes) < filterMem) return false;
  if (filterText) { const t = filterText.toLowerCase(); return base(n.exe).toLowerCase().includes(t) || String(n.pid).includes(t); }
  return true;
}
// Dim graph nodes below the filter instead of removing them, so the tree keeps its
// shape while the eye is drawn to what passes (file nodes are never filtered).
function applyGraphFilter() {
  for (const id in cards) {
    const n = nodeById[id];
    const on = !n || n.kind !== "process" || matchesFilter(n);
    cards[id].classList.toggle("fdim", !on);
  }
}

// ================= process list (left) =================
function renderList() {
  let rows = allProcs.filter(matchesFilter);
  const total = allProcs.length;
  rows.sort((a, b) => sortMode === "az" ? base(a.exe).localeCompare(base(b.exe)) : sortMode === "mem" ? (b.peak_rss_bytes || 0) - (a.peak_rss_bytes || 0) : (b.peak_cpu_pct || 0) - (a.peak_cpu_pct || 0));
  $("pcount").textContent = `${rows.length} of ${total}`;
  const el = $("plist"); el.innerHTML = "";
  for (const n of rows) {
    const nm = base(n.exe), cpu = n.peak_cpu_pct;
    const div = document.createElement("div");
    div.className = "prow" + (n.id === selectedId ? " sel" : "");
    div.innerHTML = `<span class="badge">${esc(abbrev(nm))}</span>
      <div class="info"><div class="nm">${esc(nm)}</div><div class="pid">${n.pid}</div></div>
      <div class="met"><div class="cpu" style="color:${cpuColor(cpu)}">${cpu == null ? "—" : cpu.toFixed(1) + "%"}</div><div class="mem">${humanBytes(n.peak_rss_bytes)}</div></div>`;
    div.addEventListener("click", () => focusPid(n.pid));
    el.appendChild(div);
  }
}

// ================= selection (right) =================
function selectNode(id) {
  selectedId = id;
  for (const k in cards) cards[k].classList.toggle("sel", k === id);
  const n = nodeById[id] || allProcs.find((p) => p.id === id);
  if (!n) return;
  renderList();
  if (n.kind !== "process") { showFile(n); return; }
  const cpu = n.peak_cpu_pct, pct = Math.min(100, cpu || 0);
  // open files = file nodes connected to this process in the current graph
  const files = (graphData ? graphData.edges : []).filter((e) => e.rule === "file_watch" && e.target === id)
    .map((e) => (nodeById[e.source] || {}).path).filter(Boolean);
  $("s-name").textContent = base(n.exe);
  $("s-path").className = "sel-path"; $("s-path").textContent = n.exe || "";
  $("s-body").innerHTML = `
    <dl class="kv">
      <dt>pid</dt><dd>${n.pid}</dd>
      <dt>user</dt><dd>${esc(n.user) || "—"}</dd>
      <dt>started</dt><dd>${fmtTime(n.spawn_ts)}</dd>
      <dt>peak RSS</dt><dd>${humanBytes(n.peak_rss_bytes)}</dd>
    </dl>
    <div class="cpurow"><span class="lbl">peak CPU</span><span class="val" style="color:${cpuColor(cpu)}">${cpu == null ? "—" : cpu.toFixed(1) + "%"}</span></div>
    <div class="cpubar"><i style="width:${Math.max(3, pct)}%;background:${barFor(cpu)};box-shadow:0 0 10px ${isHot(cpu) ? "rgba(255,107,107,.5)" : "rgba(125,211,252,.3)"}"></i></div>
    <div class="kick" style="margin-top:4px">Open files</div>
    <div class="files">${files.length ? files.map((f) => `<span class="fpill">${esc(base(f))}</span>`).join("") : '<span class="hint" style="color:var(--muted)">none captured</span>'}</div>
    <div class="actions"><button class="inspect" id="inspect">Inspect</button><button class="kill" id="kill">Kill −9</button></div>`;
  $("inspect").onclick = () => openInspect(n);
  $("kill").onclick = () => killPid(n.pid, base(n.exe));
}

// ---- toast ----
let snackT = 0;
function toast(msg, kind) {
  const el = $("snack");
  el.textContent = msg;
  el.classList.remove("ok", "err");
  if (kind) el.classList.add(kind);
  el.classList.add("show");
  clearTimeout(snackT);
  snackT = setTimeout(() => el.classList.remove("show"), 3200);
}

// ---- Inspect: a full "explain this process" view ----
function ancestryChain(id) {
  const chain = [], seen = new Set();
  let cur = id;
  while (cur && !seen.has(cur)) {
    seen.add(cur); chain.push(cur);
    const e = (graphData ? graphData.edges : []).find((x) => x.rule === "spawn" && x.target === cur);
    cur = e ? e.source : null;
  }
  return chain.reverse();  // root → … → this
}
const fmtPct = (v) => (v == null ? "—" : v.toFixed(1) + "%");
// tiny inline area+line chart of a value series (no libs); red when it peaks hot
function sparkline(vals, w, h) {
  vals = vals.filter((v) => v != null);
  if (vals.length < 2) return '<div class="nospark">not enough samples yet — give it a few seconds</div>';
  const max = Math.max(...vals, 1), step = w / (vals.length - 1);
  const line = vals.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`).join(" ");
  const col = max >= HOT ? "#f87171" : "#7dd3fc";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="${h}">
    <polygon points="0,${h} ${line} ${w},${h}" fill="${col}" opacity="0.10"></polygon>
    <polyline points="${line}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"></polyline>
  </svg>`;
}
// n = graph node (for lineage/causes); d = /api/proc detail (resources/children), may be null while loading
function inspectHTML(n, d) {
  const nm = base(n.exe);
  const args = (d && d.args && d.args.length) ? d.args : (n.args || []);
  const cmd = args.length ? args.join(" ") : (n.exe || "—");
  const chain = ancestryChain(n.id).map((id) => nodeById[id]).filter((x) => x && x.pid != null);
  const lineage = chain.map((x) =>
    `<span class="chip${x.id === n.id ? " self" : ""}">${esc(base(x.exe))} <i>${x.pid}</i></span>`
  ).join('<span class="arr">→</span>');
  const causes = (graphData ? graphData.edges : [])
    .filter((e) => e.rule === "file_watch" && e.target === n.id)
    .map((e) => ({ path: (nodeById[e.source] || {}).path, conf: e.confidence }))
    .filter((c) => c.path);

  let resBlock = '<div class="kick" style="margin-top:18px">Resources</div><div class="nospark">loading…</div>';
  if (d) {
    const c = d.cpu || {}, m = d.rss || {};
    const st = d.status === "running" ? '<span class="stpill run">running</span>' : '<span class="stpill">exited</span>';
    resBlock = `
      <div class="kick" style="margin-top:18px">Status</div>
      <div style="margin-top:6px">${st} <span style="color:var(--muted);font-size:12.5px">· ${d.sample_count} samples</span></div>
      <div class="kick" style="margin-top:16px">CPU over time</div>
      <div class="sparkwrap">${sparkline((d.series || []).map((s) => s.cpu), 300, 46)}</div>
      <div class="stats"><span>now <b style="color:${cpuColor(c.now)}">${fmtPct(c.now)}</b></span><span>peak <b style="color:${cpuColor(c.peak)}">${fmtPct(c.peak)}</b></span><span>avg <b>${fmtPct(c.avg)}</b></span></div>
      <div class="kick" style="margin-top:16px">Memory</div>
      <div class="stats"><span>now <b>${humanBytes(m.now)}</b></span><span>peak <b>${humanBytes(m.peak)}</b></span></div>`;
  }
  let childBlock = "";
  if (d && d.children && d.children.length) {
    const shown = d.children.slice(0, 12).map((ch) => `<span class="chip">${esc(base(ch.exe))} <i>${ch.pid}</i></span>`).join("");
    const more = d.children.length > 12 ? `<span style="color:var(--muted);font-size:12px">+${d.children.length - 12} more</span>` : "";
    childBlock = `<div class="kick" style="margin-top:18px">Spawned (${d.children.length})</div><div class="children">${shown}${more}</div>`;
  }
  return `<button class="x" aria-label="Close">×</button>
    <div class="kick">Process</div>
    <h3>${esc(nm)}</h3>
    <div class="path">${esc(n.exe || "?")}</div>
    <div class="kick" style="margin-top:18px">Command</div>
    <div class="cmd">${esc(cmd)}</div>
    <dl class="kv" style="margin-top:18px">
      <dt>pid</dt><dd>${n.pid}</dd>
      <dt>user</dt><dd>${esc(n.user) || "—"}</dd>
      <dt>started</dt><dd>${fmtTime(n.spawn_ts)}</dd>
    </dl>
    ${resBlock}
    <div class="kick" style="margin-top:18px">Lineage <span style="color:var(--dim);font-weight:500;text-transform:none;letter-spacing:0">— what launched it</span></div>
    <div class="lineage">${lineage || '<span style="color:var(--muted);font-size:12.5px">no captured parent</span>'}</div>
    ${childBlock}
    <div class="kick" style="margin-top:18px">Triggered by</div>
    ${causes.length
      ? causes.map((c) => `<div class="cause"><span class="fpath">${esc(c.path)}</span><span class="conf">${c.conf != null ? "conf " + c.conf.toFixed(2) : ""}</span></div>`).join("")
      : '<div style="color:var(--muted);font-size:12.5px">no file cause captured</div>'}`;
}
async function openInspect(n) {
  $("modal").classList.add("show");
  $("sheet").innerHTML = inspectHTML(n, null);   // instant render; resources fill in
  $("sheet").querySelector(".x").onclick = closeInspect;
  try {
    const d = await (await fetch(`/api/proc?pid=${n.pid}`)).json();
    if (!d.error && $("modal").classList.contains("show")) {
      $("sheet").innerHTML = inspectHTML(n, d);
      $("sheet").querySelector(".x").onclick = closeInspect;
    }
  } catch (_) { /* keep the instant view */ }
}
function closeInspect() { $("modal").classList.remove("show"); }
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeInspect(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeInspect(); });
function showFile(n) {
  $("s-name").textContent = base(n.path); $("s-path").className = "sel-path"; $("s-path").textContent = n.path || "";
  const conf = (graphData.edges.find((e) => e.rule === "file_watch" && e.source === n.id) || {}).conf;
  $("s-body").innerHTML = `<span class="fpill">file</span>
    <p style="color:var(--muted);margin-top:14px;line-height:1.6">A change to this file likely triggered the connected process${conf != null ? ` — confidence <b style="color:var(--acc-2)">${conf.toFixed(2)}</b>` : ""}.</p>`;
}

// pick a process: re-centre the graph on its family, then select it
async function focusPid(pid) {
  overlay("loading");
  currentPid = pid;  // remember what the graph shows, so live refresh re-polls it
  try {
    const r = await fetch(`/api/graph?pid=${pid}&family=1&min_confidence=${$("minc").value}`);
    const d = await r.json();
    if (!r.ok || d.error) { setError(d.error || `error ${r.status}`); return; }
    renderGraph(d);
    const hit = d.nodes.find((x) => x.pid === pid && x.kind === "process");
    selectNode(hit ? hit.id : d.culprit);
    if (window.innerWidth <= 660) openNav(false);  // reveal the graph after picking on a phone
  } catch (e) { setError(`request failed: ${e}`); }
}
async function killPid(pid, name) {
  if (!confirm(`Kill ${name} (pid ${pid}) with SIGKILL?\nThis cannot be undone.`)) return;
  try {
    const r = await fetch(`/api/kill?pid=${pid}`, { method: "POST", headers: { "X-CauseGraph": "1" } });
    const d = await r.json();
    if (!r.ok || d.error) { toast(d.error || `kill failed (${r.status})`, "err"); return; }
    toast(`Killed ${name} (pid ${pid})`, "ok");
    setTimeout(refreshData, 500);  // refresh in place — stay on this neighborhood, don't jump
  } catch (e) { toast(`request failed: ${e}`, "err"); }
}

// ================= boot + controls =================
async function loadAll() {
  try { const m = await (await fetch("/api/meta")).json(); if (m.db) { $("dbname").textContent = m.db; $("art-db").textContent = m.db; } if (m.latest_ts != null) lastTs = m.latest_ts; } catch (_) {}
  try {
    const r = await fetch("/api/graph?all=1&max_nodes=20000");  // the list wants every process, not the graph cap
    const d = await r.json();
    allProcs = (d.nodes || []).filter((n) => n.kind === "process");
    $("art-sub").textContent = `sqlite · ${allProcs.length} processes${d.truncated ? "+" : ""}`;
    renderList();
    if (!allProcs.length) { overlay("empty"); return; }
    // default view: the hottest process that has a real tree (ancestry / children /
    // file cause), so we land on something meaningful rather than a lone node.
    overlay("loading");
    const cands = allProcs.slice().sort((a, b) => (b.peak_cpu_pct || 0) - (a.peak_cpu_pct || 0)).slice(0, 8);
    for (const c of cands) {
      try {
        const r = await fetch(`/api/graph?pid=${c.pid}&family=1&min_confidence=0.5`);
        const d = await r.json();
        if (r.ok && !d.error && d.nodes.length > 1) { currentPid = c.pid; renderGraph(d); const hit = d.nodes.find((x) => x.pid === c.pid && x.kind === "process"); selectNode(hit ? hit.id : d.culprit); return; }
      } catch (_) {}
    }
    focusPid(cands[0].pid);  // fallback: hottest (may be a lone node)
  } catch (_) { overlay("empty"); }
}

// Search is a pure filter. Enter focuses the top match: a number -> that pid;
// text -> the highest-ranked process currently matching the filter.
$("f").addEventListener("submit", (e) => {
  e.preventDefault(); const v = $("query").value.trim(); if (!v) return;
  if (/^\d+$/.test(v)) { focusPid(parseInt(v, 10)); return; }
  const top = allProcs.filter(matchesFilter).sort((a, b) => (b.peak_cpu_pct || 0) - (a.peak_cpu_pct || 0))[0];
  if (top) focusPid(top.pid);
});
$("query").addEventListener("input", (e) => { filterText = e.target.value.trim(); renderList(); applyGraphFilter(); });
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; sortMode = b.dataset.sort; [...e.currentTarget.children].forEach((c) => c.classList.toggle("on", c === b)); renderList(); });
$("cpuMin").addEventListener("input", (e) => { filterCpu = +e.target.value; $("cpuLbl").textContent = `${filterCpu}%`; renderList(); applyGraphFilter(); });
$("memMin").addEventListener("input", (e) => { filterMem = +e.target.value; $("memLbl").textContent = `${filterMem} MiB`; renderList(); applyGraphFilter(); });
$("reset").addEventListener("click", () => { filterCpu = 0; filterMem = 0; filterText = ""; $("cpuMin").value = 0; $("memMin").value = 0; $("query").value = ""; $("cpuLbl").textContent = "0%"; $("memLbl").textContent = "0 MiB"; renderList(); applyGraphFilter(); });
document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("query").focus(); } });
// Collapse the inspector to give the graph full width. cytoscape can't detect its
// container resizing, so resize + refit once the width transition (.18s) settles.
$("panelToggle").addEventListener("click", () => {
  const collapsed = document.querySelector("main").classList.toggle("rcollapsed");
  $("panelToggle").title = collapsed ? "Show inspector" : "Hide inspector";
  syncFor(500);
  setTimeout(() => { cy.resize(); refit(); }, 210);
});

// ---- responsive: process-list drawer + graph refit on resize ----
const NARROW = 940;                       // below this the inspector is a slide-over
const mainEl = document.querySelector("main");
function openNav(on) { mainEl.classList.toggle("lnav", on); $("scrim").classList.toggle("show", on); }
$("menuBtn").addEventListener("click", () => openNav(!mainEl.classList.contains("lnav")));
$("scrim").addEventListener("click", () => openNav(false));
// On narrow screens the inspector floats over the graph — start it hidden so the
// graph owns the width; the toggle tab pulls it in on demand.
if (window.innerWidth <= NARROW) mainEl.classList.add("rcollapsed");
// Keep the graph fitted as the window (or panels) resize. cytoscape can't observe
// its own container, so drive resize+refit ourselves, debounced to a frame settle.
let rzT = 0;
window.addEventListener("resize", () => {
  clearTimeout(rzT);
  rzT = setTimeout(() => { cy.resize(); refit(); if (window.innerWidth > 660) openNav(false); }, 120);
});

// ---- live auto-refresh ----
// panning the canvas counts as "interacting" — don't refresh mid-drag.
$("cy").addEventListener("pointerdown", () => { panActive = true; });
window.addEventListener("pointerup", () => { panActive = false; });

// Never refresh mid-interaction, so the view can't jump under the user's hands.
function busy() {
  return refreshing || hoverActive || panActive || document.hidden
    || document.activeElement === $("query")
    || mainEl.classList.contains("lnav");
}

// Same node set -> update metrics in place (no relayout, no yank). Topology changed
// (process died / spawned) -> a full re-render, accepting one re-fit.
function applyGraphUpdate(d) {
  const ids = Object.keys(nodeById);
  const sameSet = graphData && d.nodes.length === ids.length && d.nodes.every((n) => nodeById[n.id]);
  if (!sameSet) { renderGraph(d); selectNode(selectedId && nodeById[selectedId] ? selectedId : d.culprit); return; }
  graphData = d;
  for (const n of d.nodes) {
    nodeById[n.id] = n;
    const card = cards[n.id]; if (!card) continue;
    card.innerHTML = nodeCardHTML(n);
    card.classList.toggle("hot", n.kind === "process" && isHot(n.peak_cpu_pct));
  }
  applyGraphFilter();
  $("status").textContent = `observing · ${d.nodes.length} nodes · ${d.edges.length} edges` + (d.truncated ? " · truncated" : "");
  if (selectedId && nodeById[selectedId]) selectNode(selectedId);  // refresh inspector numbers
}

async function refreshData() {
  refreshing = true;
  const sc = $("plist").scrollTop;
  try {
    const rl = await fetch("/api/graph?all=1&max_nodes=20000");
    const dl = await rl.json();
    if (rl.ok && !dl.error) {
      allProcs = (dl.nodes || []).filter((n) => n.kind === "process");
      $("art-sub").textContent = `sqlite · ${allProcs.length} processes${dl.truncated ? "+" : ""}`;
      renderList();
    }
    if (currentPid != null) {
      const rg = await fetch(`/api/graph?pid=${currentPid}&family=1&min_confidence=${$("minc").value}`);
      const dg = await rg.json();
      if (rg.ok && !dg.error) applyGraphUpdate(dg);
    }
    try { noteTs((await (await fetch("/api/meta")).json()).latest_ts); } catch (_) {}
  } catch (_) { /* transient; next tick retries */ }
  finally { refreshing = false; $("plist").scrollTop = sc; }
}

function autoTick() { if (!busy()) refreshData(); }  // skip this beat if mid-interaction

// Did the newest event move since last poll? If not for 2 polls, the recorder stopped.
function noteTs(ts) {
  if (ts != null && (lastTs == null || ts > lastTs)) { lastTs = ts; staleCount = 0; frozen = false; }
  else if (++staleCount >= 2) frozen = true;
  updateLiveLabel();
}
function updateLiveLabel() {
  const chip = document.querySelector(".dbchip"), live = $("live");
  if (autoMs === 0) {
    live.textContent = "paused"; live.className = "livebtn"; live.title = "Auto-refresh off — click to resume";
    chip.classList.add("paused"); chip.classList.remove("frozen");
  } else if (frozen) {
    live.textContent = "frozen"; live.className = "livebtn frozen";
    live.title = "Polling, but no recorder is writing — data isn't updating. Run cged to capture live.";
    chip.classList.add("frozen"); chip.classList.remove("paused");
  } else {
    live.textContent = `live · ${autoMs / 1000}s`; live.className = "livebtn on"; live.title = "Live — click to change interval";
    chip.classList.remove("paused", "frozen");
  }
}

function setAuto(ms) {
  const wasOff = autoMs === 0;
  autoMs = ms;
  if (autoTimer) { clearInterval(autoTimer); autoTimer = 0; }
  if (ms > 0) { autoTimer = setInterval(autoTick, ms); if (wasOff) { staleCount = 0; frozen = false; } }  // fresh chance on resume
  updateLiveLabel();
}
$("live").addEventListener("click", () => setAuto(AUTO_STEPS[(AUTO_STEPS.indexOf(autoMs) + 1) % AUTO_STEPS.length]));

loadAll();
setAuto(autoMs);  // start the live poll (first tick one interval from now)
