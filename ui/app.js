/* CauseGraph — three-pane viewer. cytoscape lays out + draws connectors; every
   node is an HTML card. The engine does the reasoning; this only presents it. */
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
const base = (p) => (p || "").split("/").filter(Boolean).pop() || p || "?";

// ---- visual helpers ----
const BADGE = ["#494571", "#3d5578", "#3a5f57", "#5c4560", "#5c5238", "#454b57", "#5c3f47", "#3f5566"];
function badgeColor(name) { let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return BADGE[h % BADGE.length]; }
function abbrev(name) {
  const parts = name.replace(/\.[a-z0-9]+$/i, "").replace(/[^A-Za-z0-9]+/g, " ").trim().split(/(?<=[a-z])(?=[A-Z])|\s+/).filter(Boolean);
  return (parts.length >= 2 ? parts[0][0] + parts[1][0] : (name.replace(/[^A-Za-z0-9]/g, "") + "?").slice(0, 2)).toLowerCase();
}
function cpuColor(c) { c = c || 0; return c >= 75 ? "#ef6b66" : c >= 25 ? "#e0a83c" : c >= 10 ? "#6f93bf" : "#74787f"; }
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

// ================= graph (cytoscape + HTML cards) =================
const cy = cytoscape({
  container: $("cy"), minZoom: 0.2, maxZoom: 1.5, wheelSensitivity: 0.22,
  style: [
    { selector: "node", style: { "width": "data(w)", "height": "data(h)", "shape": "round-rectangle", "background-opacity": 0, "border-width": 0, "events": "no" } },
    { selector: "edge", style: {
        "curve-style": "bezier", "width": 1.6, "line-color": "data(col)", "opacity": 0.9,
        "line-style": "dashed", "line-dash-pattern": [5, 6], "line-dash-offset": 0 } },
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
  return `<div class="r1"><span class="badge" style="background:${badgeColor(nm)}">${esc(abbrev(nm))}</span>
      <span class="nm">${esc(nm)}</span><span class="sdot"></span>
      <span class="pc" style="color:${cpuColor(cpu)}">${cpu == null ? "—" : cpu.toFixed(1) + "%"}</span></div>
      <div class="pid">pid ${n.pid}</div>
      <div class="bar"><i style="width:${Math.min(100, cpu || 0)}%;background:${cpuColor(cpu)}"></i></div>`;
}
function buildCards() {
  layer.innerHTML = ""; cards = {};
  cy.nodes().forEach((cyn) => {
    const n = cyn.data("meta");
    const div = document.createElement("div");
    div.className = "gnode" + (n.kind === "file" ? " file" : "") + (n.id === graphData.culprit ? " hot" : "");
    div.innerHTML = nodeCardHTML(n);
    div.addEventListener("click", (e) => { e.stopPropagation(); selectNode(n.id); });
    div.addEventListener("mouseenter", () => hoverNode(cyn));
    div.addEventListener("mouseleave", unhover);
    layer.appendChild(div); cards[n.id] = div;
  });
  positionCards();
}
function positionCards() {
  const z = cy.zoom();
  cy.nodes().forEach((cyn) => { const d = cards[cyn.id()]; if (!d) return; const p = cyn.renderedPosition(); d.style.transform = `translate(${p.x}px,${p.y}px) translate(-50%,-50%) scale(${z})`; });
}
cy.on("pan zoom", positionCards); cy.on("position", "node", positionCards);
function syncFor(ms) { const end = performance.now() + ms; (function t() { positionCards(); if (performance.now() < end) requestAnimationFrame(t); })(); }

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
    const w = n.kind === "file" ? 178 : 208, h = n.kind === "file" ? 52 : 78;
    els.push({ data: { id: n.id, meta: n, w, h } });
  }
  for (const e of data.edges) els.push({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target, conf: e.conf, rule: e.rule, col: e.rule === "file_watch" ? "#c99539" : "#5f7796" } });
  cy.elements().remove(); cy.add(els);
  const lay = cy.layout({ name: "breadthfirst", directed: true, padding: 40, spacingFactor: 1.45, avoidOverlap: true, animate: true, animationDuration: 380, animationEasing: "ease-out" });
  lay.one("layoutstop", () => { cy.animate({ fit: { padding: 80 }, duration: 300 }); syncFor(760); });
  lay.run(); buildCards(); syncFor(900); animateEdges(); overlay(null);
  $("hcount").textContent = ` · ${data.nodes.length} node${data.nodes.length === 1 ? "" : "s"}`;
  $("status").textContent = `observing · ${data.nodes.length} nodes · ${data.edges.length} edges` + (data.truncated ? " · truncated" : "");
}
function hoverNode(cyn) { const hi = cyn.closedNeighborhood(); const keep = new Set(hi.nodes().map((x) => x.id())); cy.edges().style("opacity", 0.08); hi.edges().style("opacity", 0.95); for (const id in cards) cards[id].classList.toggle("dim", !keep.has(id)); }
function unhover() { cy.edges().style("opacity", 0.9); for (const id in cards) cards[id].classList.remove("dim"); }
cy.on("tap", (e) => { if (e.target === cy) { /* keep selection */ } });

// ================= process list (left) =================
function renderList() {
  let rows = allProcs.filter((n) => (n.peak_cpu_pct || 0) >= filterCpu && mib(n.peak_rss_bytes) >= filterMem);
  if (filterText) { const t = filterText.toLowerCase(); rows = rows.filter((n) => base(n.exe).toLowerCase().includes(t) || String(n.pid).includes(t)); }
  const total = allProcs.length;
  rows.sort((a, b) => sortMode === "az" ? base(a.exe).localeCompare(base(b.exe)) : sortMode === "mem" ? (b.peak_rss_bytes || 0) - (a.peak_rss_bytes || 0) : (b.peak_cpu_pct || 0) - (a.peak_cpu_pct || 0));
  $("pcount").textContent = `${rows.length} of ${total}`;
  const el = $("plist"); el.innerHTML = "";
  for (const n of rows) {
    const nm = base(n.exe), cpu = n.peak_cpu_pct;
    const div = document.createElement("div");
    div.className = "prow" + (n.id === selectedId ? " sel" : "");
    div.innerHTML = `<span class="badge" style="background:${badgeColor(nm)}">${esc(abbrev(nm))}</span>
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
    <div class="cpubar"><i style="width:${pct}%"></i></div>
    <div class="kick" style="margin-top:4px">Open files</div>
    <div class="files">${files.length ? files.map((f) => `<span class="fpill">${esc(base(f))}</span>`).join("") : '<span class="hint" style="color:var(--muted)">none captured</span>'}</div>
    <div class="actions"><button id="inspect">Inspect</button><button class="kill" id="kill">Kill −9</button></div>`;
  $("inspect").onclick = () => focusPid(n.pid);
  $("kill").onclick = () => killPid(n.pid, base(n.exe));
}
function showFile(n) {
  $("s-name").textContent = base(n.path); $("s-path").className = "sel-path"; $("s-path").textContent = n.path || "";
  const conf = (graphData.edges.find((e) => e.rule === "file_watch" && e.source === n.id) || {}).conf;
  $("s-body").innerHTML = `<span class="fpill">file</span>
    <p style="color:var(--muted);margin-top:14px;line-height:1.6">A change to this file likely triggered the connected process${conf != null ? ` — confidence <b style="color:var(--amber)">${conf.toFixed(2)}</b>` : ""}.</p>`;
}

// pick a process: re-centre the graph on its family, then select it
async function focusPid(pid) {
  overlay("loading");
  try {
    const r = await fetch(`/api/graph?pid=${pid}&family=1&min_confidence=${$("minc").value}`);
    const d = await r.json();
    if (!r.ok || d.error) { setError(d.error || `error ${r.status}`); return; }
    renderGraph(d);
    const hit = d.nodes.find((x) => x.pid === pid && x.kind === "process");
    selectNode(hit ? hit.id : d.culprit);
  } catch (e) { setError(`request failed: ${e}`); }
}
async function killPid(pid, name) {
  if (!confirm(`Kill ${name} (pid ${pid}) with SIGKILL?\nThis cannot be undone.`)) return;
  try {
    const r = await fetch(`/api/kill?pid=${pid}`, { method: "POST", headers: { "X-CauseGraph": "1" } });
    const d = await r.json();
    if (!r.ok || d.error) { alert(d.error || `failed (${r.status})`); return; }
    setTimeout(loadAll, 400);
  } catch (e) { alert(`request failed: ${e}`); }
}

// ================= boot + controls =================
async function loadAll() {
  try { const m = await (await fetch("/api/meta")).json(); if (m.db) { $("dbname").textContent = m.db; $("art-db").textContent = m.db; } } catch (_) {}
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
        if (r.ok && !d.error && d.nodes.length > 1) { renderGraph(d); const hit = d.nodes.find((x) => x.pid === c.pid && x.kind === "process"); selectNode(hit ? hit.id : d.culprit); return; }
      } catch (_) {}
    }
    focusPid(cands[0].pid);  // fallback: hottest (may be a lone node)
  } catch (_) { overlay("empty"); }
}

$("f").addEventListener("submit", (e) => {
  e.preventDefault(); const v = $("query").value.trim(); if (!v) return;
  if (/^\d+$/.test(v)) focusPid(parseInt(v, 10));
  else fetch(`/api/graph?q=${encodeURIComponent(v)}&family=1&min_confidence=${$("minc").value}`).then((r) => r.json()).then((d) => { if (d.error) setError(d.error); else { renderGraph(d); selectNode(d.culprit); } });
});
$("query").addEventListener("input", (e) => { filterText = e.target.value.trim(); renderList(); });
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; sortMode = b.dataset.sort; [...e.currentTarget.children].forEach((c) => c.classList.toggle("on", c === b)); renderList(); });
$("cpuMin").addEventListener("input", (e) => { filterCpu = +e.target.value; $("cpuLbl").textContent = `${filterCpu}%`; renderList(); });
$("memMin").addEventListener("input", (e) => { filterMem = +e.target.value; $("memLbl").textContent = `${filterMem} MiB`; renderList(); });
$("reset").addEventListener("click", () => { filterCpu = 0; filterMem = 0; filterText = ""; $("cpuMin").value = 0; $("memMin").value = 0; $("query").value = ""; $("cpuLbl").textContent = "0%"; $("memLbl").textContent = "0 MiB"; renderList(); });
document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("query").focus(); } });

loadAll();
