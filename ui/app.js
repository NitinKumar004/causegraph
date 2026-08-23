/* CauseGraph — three-pane viewer. cytoscape lays out + draws connectors; every
   node is an HTML card. The engine does the reasoning; this only presents it. */
"use strict";
const $ = (id) => document.getElementById(id);
// Escapes for HTML text AND attribute contexts (single quote + backtick included, so a value
// dropped into a single-quoted or templated attribute can't break out).
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"'`]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" }[c])));
const base = (p) => (p || "").split("/").filter(Boolean).pop() || p || "?";
// Append the timeline position (as_of, ns) to any API URL when rewound; no-op when live.
const asq = (url) => (asOf == null ? url : url + (url.includes("?") ? "&" : "?") + "as_of=" + asOf);
// A small "i" help marker. `t` is plain help text (may contain <b>/<code>); never put a literal
// double-quote in it — it lives in an attribute. Any element with data-help gets the same popover.
const H = (t) => `<span class="help" tabindex="0" role="button" aria-label="What's this?" data-help="${t}"></span>`;

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
// "current" = latest sample, falling back to peak when a process was only seen at baseline
function curCpu(n) { return n.cpu_pct != null ? n.cpu_pct : (n.peak_cpu_pct || 0); }
function curMem(n) { return n.rss_bytes != null ? n.rss_bytes : n.peak_rss_bytes; }
// group by application: the first "…/<Name>.app/…" bundle (folds all Chrome helpers into
// "Google Chrome"); else the binary's basename.
function appOf(exe) { const m = (exe || "").match(/\/([^/]+)\.app\//); return m ? m[1] : base(exe); }
// "alive now" = sampled within a few polls of the newest event; filters out the hours of
// exited processes the capture also holds, so roll-ups/summary reflect NOW, not history.
const ALIVE_GRACE_NS = 8e9;
// "now" is the newest event when live, or the timeline position when rewound.
function nowTs() { return asOf != null ? asOf : lastTs; }
function alive(n) { const t = nowTs(); return t == null || (n.last_seen_ts != null && n.last_seen_ts >= t - ALIVE_GRACE_NS); }
function fmtTime(ns) { if (!ns) return "—"; const d = new Date(ns / 1e6); return d.toTimeString().slice(0, 8); }

// ---- state ----
let allProcs = [];       // every process node (for the list), from ?all=1
let graphData = null;    // current graph subset
let selectedId = null;
let sortMode = "cpu";
let groupMode = "app";   // "app" (roll-ups) or "proc" (flat per-PID list)
let filterCpu = 0, filterMem = 0, filterText = "";
const nodeById = {};     // id -> node meta from the current graph
let currentPid = null;   // the pid whose family the graph currently shows (for live refresh)
let expanded = false;    // whether the current family view is the widened ("show more") one
let asOf = null;         // timeline: view the machine as of this ts (ns); null = live/now
let tlMin = null, tlMax = null;  // capture window (ns) the timeline scrubber spans
let hideHelp = () => {};  // set by the help system; dismisses the popover (called before re-renders)
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
  const cpu = curCpu(n);
  return `<div class="r1"><span class="badge">${esc(abbrev(nm))}</span>
      <span class="nm">${esc(nm)}</span><span class="sdot"></span>
      <span class="pc" style="color:${cpuColor(cpu)}">${cpu.toFixed(1)}%</span></div>
      <div class="pid">pid ${n.pid}</div>
      <div class="bar"><i style="width:${Math.max(3, Math.min(100, cpu))}%;background:${barFor(cpu)}"></i></div>`;
}
function buildCards() {
  layer.innerHTML = ""; cards = {};
  cy.nodes().forEach((cyn) => {
    const n = cyn.data("meta");
    const div = document.createElement("div");
    div.className = "gnode" + (n.kind === "file" ? " file" : "") + (n.kind === "process" && isHot(curCpu(n)) ? " hot" : "");
    div.innerHTML = nodeCardHTML(n);
    div.addEventListener("click", (e) => { e.stopPropagation(); selectNode(n.id); ensurePanelOpen(); });
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
  setStatus(data);
}
// footer status; when the view was capped, offer a clickable "show more" that widens it
function setStatus(d) {
  let s = `observing · ${d.nodes.length} nodes · ${d.edges.length} edges`;
  if (d.truncated) s += expanded ? " · showing more" : ` · <span id="expandLink" class="expandlink">show more</span>`;
  $("status").innerHTML = s;
  const l = $("expandLink"); if (l) l.onclick = expandCurrent;
}
async function expandCurrent() {
  if (currentPid == null) return;
  expanded = true;
  try {
    const r = await fetch(asq(`/api/graph?pid=${currentPid}&family=1&expand=1&min_confidence=${$("minc").value}`));
    const d = await r.json();
    if (r.ok && !d.error) { renderGraph(d); selectNode(selectedId && nodeById[selectedId] ? selectedId : d.culprit); }
  } catch (_) { /* keep current view */ }
}
function hoverNode(cyn) { hoverActive = true; const hi = cyn.closedNeighborhood(); const keep = new Set(hi.nodes().map((x) => x.id())); cy.edges().style("opacity", 0.08); hi.edges().style("opacity", 0.95); for (const id in cards) cards[id].classList.toggle("dim", !keep.has(id)); }
function unhover() { hoverActive = false; cy.edges().style("opacity", 0.9); for (const id in cards) cards[id].classList.remove("dim"); }
cy.on("tap", (e) => { if (e.target === cy) { /* keep selection */ } });

// A process passes the CPU/MEM/name filter — the one predicate both the list and
// the graph obey, so sliding a filter dims the same nodes in both places.
function matchesFilter(n) {
  if (curCpu(n) < filterCpu || mib(curMem(n)) < filterMem) return false;
  if (filterText) { const t = filterText.toLowerCase(); return base(n.exe).toLowerCase().includes(t) || appOf(n.exe).toLowerCase().includes(t) || String(n.pid).includes(t); }
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
const bySort = (getCpu, getMem, getName) => (a, b) =>
  sortMode === "az" ? getName(a).localeCompare(getName(b))
  : sortMode === "mem" ? (getMem(b) || 0) - (getMem(a) || 0)
  : (getCpu(b) || 0) - (getCpu(a) || 0);

// The one-line machine summary, as data: {tone, top:[[app,cpu]...], text}. Shared by the
// header status line and the exported report so both tell the same story.
function statusSummary() {
  const groups = Object.create(null);  // null proto: an app literally named "__proto__" can't poison Object.prototype
  for (const n of allProcs.filter(alive)) { const k = appOf(n.exe); (groups[k] = groups[k] || { cpu: 0 }).cpu += curCpu(n); }
  const top = Object.entries(groups).sort((a, b) => b[1].cpu - a[1].cpu).filter(([, g]) => g.cpu >= 5).slice(0, 2);
  if (!top.length) return { tone: "quiet", top: [], text: "Quiet — nothing's working hard right now." };
  const names = top.map(([app, g]) => `${app} (${g.cpu.toFixed(0)}%)`);
  const who = names.length === 2 ? `${names[0]} and ${names[1]}` : names[0];
  const busy = top[0][1].cpu >= 100;
  return { tone: busy ? "busy" : "active", top, text: `${busy ? "Busy" : "Active"} — ${who} ${names.length === 2 ? "are" : "is"} working hardest.` };
}
function renderStatus() {
  const el = $("mstatus"); if (!el) return;
  const s = statusSummary();
  el.className = "mstatus" + (s.tone === "quiet" ? " quiet" : s.tone === "busy" ? " busy" : "");
  if (s.tone === "quiet") { el.innerHTML = `<span class="dotq"></span>${esc(s.text)}`; return; }
  const parts = s.top.map(([app, g]) => `<b>${esc(app)}</b> (${g.cpu.toFixed(0)}%)`);
  const who = parts.length === 2 ? `${parts[0]} and ${parts[1]}` : parts[0];
  el.innerHTML = `<span class="dotq"></span>${s.tone === "busy" ? "Busy" : "Active"} — ${who} ${parts.length === 2 ? "are" : "is"} working hardest.`;
}

function renderList() {
  renderStatus();
  const live = allProcs.filter(alive);
  const rows = live.filter(matchesFilter);
  $("pcount").textContent = groupMode === "app"
    ? `${new Set(rows.map((n) => appOf(n.exe))).size} apps`
    : `${rows.length} of ${live.length}`;
  const el = $("plist"); el.innerHTML = "";
  if (groupMode === "app") renderAppRows(el, rows); else renderProcRows(el, rows);
}

function renderProcRows(el, rows) {
  rows.sort(bySort(curCpu, curMem, (n) => base(n.exe)));
  for (const n of rows) {
    const nm = base(n.exe), cpu = curCpu(n);
    const div = document.createElement("div");
    div.className = "prow" + (n.id === selectedId ? " sel" : "");
    div.innerHTML = `<span class="badge">${esc(abbrev(nm))}</span>
      <div class="info"><div class="nm">${esc(nm)}</div><div class="pid">${n.pid}</div></div>
      <div class="met"><div class="cpu" style="color:${cpuColor(cpu)}">${cpu.toFixed(1)}%</div><div class="mem">${humanBytes(curMem(n))}</div></div>`;
    div.addEventListener("click", () => { focusPid(n.pid); ensurePanelOpen(); });
    el.appendChild(div);
  }
}

function renderAppRows(el, rows) {
  const groups = Object.create(null);  // app -> {cpu, mem, procs[]}; null proto (see statusSummary)
  for (const n of rows) {
    const k = appOf(n.exe);
    const g = groups[k] || (groups[k] = { app: k, cpu: 0, mem: 0, procs: [] });
    g.cpu += curCpu(n); g.mem += curMem(n) || 0; g.procs.push(n);
  }
  const list = Object.values(groups).sort(bySort((g) => g.cpu, (g) => g.mem, (g) => g.app));
  for (const g of list) {
    const hottest = g.procs.slice().sort((a, b) => curCpu(b) - curCpu(a))[0];
    const sel = g.procs.some((n) => n.id === selectedId);
    const div = document.createElement("div");
    div.className = "prow" + (sel ? " sel" : "");
    div.innerHTML = `<span class="badge">${esc(abbrev(g.app))}</span>
      <div class="info"><div class="nm">${esc(g.app)}</div><div class="pid">${g.procs.length} process${g.procs.length === 1 ? "" : "es"}</div></div>
      <div class="met"><div class="cpu" style="color:${cpuColor(g.cpu)}">${g.cpu.toFixed(1)}%</div><div class="mem">${humanBytes(g.mem)}</div></div>`;
    div.addEventListener("click", () => { focusPid(hottest.pid); ensurePanelOpen(); });
    el.appendChild(div);
  }
}

// ================= selection (right) =================
// The always-visible inspector body: identity + live CPU & memory trend graphs (the two
// things a user needs to judge a process) + open files. d is the /api/proc detail (null
// while it loads). Full command / lineage / children live in the Inspect modal.
function inspectorBody(n, d) {
  const files = (graphData ? graphData.edges : []).filter((e) => e.rule === "file_watch" && e.target === n.id)
    .map((e) => (nodeById[e.source] || {}).path).filter(Boolean);
  let res = '<div class="nospark" style="margin-top:14px">loading resource history…</div>';
  if (d) {
    const c = d.cpu || {}, m = d.rss || {};
    const st = d.status === "running" ? '<span class="stpill run">running</span>' : '<span class="stpill">exited</span>';
    res = `
      <div style="margin-top:14px">${st}${H("Whether this program is still running, or has already quit. Based on whether we saw it in the most recent samples.")} <span style="color:var(--muted);font-size:12px">· ${d.sample_count} samples</span></div>
      <div class="kick" style="margin-top:16px">CPU${H("Share of one processor core this program used. <b>100%</b> = one core fully busy; a program spread across several cores can go above 100%. <b>now</b> is the latest reading, <b>peak</b> the highest, <b>avg</b> the average over the recorded window.")}</div>
      <div class="sparkwrap">${sparkline((d.series || []).map((s) => s.cpu), 260, 44, true)}</div>
      <div class="stats"><span>now <b style="color:${cpuColor(c.now)}">${fmtPct(c.now)}</b></span><span>peak <b style="color:${cpuColor(c.peak)}">${fmtPct(c.peak)}</b></span><span>avg <b>${fmtPct(c.avg)}</b></span></div>
      <div class="kick" style="margin-top:16px">Memory${H("How much memory (RAM) this program is holding. <b>now</b> is the latest reading; <b>peak</b> is the most it ever held during the recording.")}</div>
      <div class="sparkwrap">${sparkline((d.series || []).map((s) => s.rss), 260, 44, false)}</div>
      <div class="stats"><span>now <b>${humanBytes(m.now)}</b></span><span>peak <b>${humanBytes(m.peak)}</b></span></div>`;
  }
  return `
    <dl class="kv">
      <dt>pid</dt><dd>${n.pid}</dd>
      <dt>user</dt><dd>${esc(n.user) || "—"}</dd>
      <dt>started</dt><dd>${fmtTime(n.spawn_ts)}</dd>
    </dl>
    ${res}
    <div class="kick" style="margin-top:16px">Open files${H("Files this program touched that the recorder captured. A file here that changed just before the program reacted is drawn as a dashed <b>file → program</b> link in the graph.")}</div>
    <div class="files">${files.length ? files.map((f) => `<span class="fpill">${esc(base(f))}</span>`).join("") : '<span class="hint" style="color:var(--muted)">none captured</span>'}</div>
    <div class="actions"><button class="inspect" id="inspect">Inspect</button><button class="kill" id="kill" data-help="Force-quit this program (sends SIGKILL). It stops immediately — any unsaved work in it is lost. Asks you to confirm first.">Kill −9</button></div>`;
}
function wireInspector(n) {
  $("inspect").onclick = () => openInspect(n);
  $("kill").onclick = () => killPid(n.pid, base(n.exe));
}
async function selectNode(id) {
  const changed = selectedId !== id;
  selectedId = id;
  for (const k in cards) cards[k].classList.toggle("sel", k === id);
  const n = nodeById[id] || allProcs.find((p) => p.id === id);
  if (!n) return;
  renderList();
  if (n.kind !== "process") { showFile(n); return; }
  $("s-name").textContent = base(n.exe);
  $("s-path").className = "sel-path"; $("s-path").textContent = n.exe || "";
  // Only paint the "loading…" placeholder on a fresh selection. On a live-poll re-select of the
  // SAME process, keep the current graphs on screen until the fetch resolves — no 4s flicker.
  if (changed || !$("s-body").querySelector(".sparkwrap")) {
    hideHelp();  // the help "i" markers inside are about to be replaced
    $("s-body").innerHTML = inspectorBody(n, null);
    wireInspector(n);
  }
  try {
    const d = await (await fetch(asq(`/api/proc?pid=${n.pid}`))).json();
    if (!d.error && selectedId === id) { hideHelp(); $("s-body").innerHTML = inspectorBody(n, d); wireInspector(n); }
  } catch (_) { /* keep the instant view */ }
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
function sparkline(vals, w, h, hotAware) {
  vals = vals.filter((v) => v != null);
  if (vals.length < 2) return '<div class="nospark">not enough samples yet — give it a few seconds</div>';
  const max = Math.max(...vals, 1), step = w / (vals.length - 1);
  const line = vals.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * (h - 4) - 2).toFixed(1)}`).join(" ");
  const col = (hotAware && max >= HOT) ? "#f87171" : "#7dd3fc";  // only CPU turns red at a hot peak
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
      <div class="sparkwrap">${sparkline((d.series || []).map((s) => s.cpu), 300, 46, true)}</div>
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
let inspectPid = null;  // which process the Inspect drawer currently shows (guards stale fetches)
async function openInspect(n) {
  inspectPid = n.pid;
  $("modal").classList.add("show");
  $("sheet").innerHTML = inspectHTML(n, null);   // instant render; resources fill in
  $("sheet").querySelector(".x").onclick = closeInspect;
  try {
    const d = await (await fetch(asq(`/api/proc?pid=${n.pid}`))).json();
    // refill only if the drawer is still open AND still showing THIS process — a slower fetch
    // for a previously-inspected process must not overwrite the one now on screen.
    if (!d.error && $("modal").classList.contains("show") && inspectPid === n.pid) {
      $("sheet").innerHTML = inspectHTML(n, d);
      $("sheet").querySelector(".x").onclick = closeInspect;
    }
  } catch (_) { /* keep the instant view */ }
}
function closeInspect() { inspectPid = null; hideHelp(); $("modal").classList.remove("show"); }
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeInspect(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeInspect(); closeIncidents(); } });

// ---- incident feed ----
let incidentList = [];
async function fetchIncidents() {
  try {
    const d = await (await fetch(asq("/api/incidents"))).json();
    if (d.incidents) { incidentList = d.incidents; $("incCount").textContent = d.total; $("incBtn").classList.toggle("has", d.total > 0); }
  } catch (_) {}
}
function openIncidents() {
  const s = $("incidents-sheet");
  const glyph = { cpu: "%", leak: "M", crashloop: "⟳" };
  const cards = incidentList.length
    ? incidentList.map((i) => `<div class="inc-card${i.pid == null ? " static" : ""}"${i.pid == null ? "" : ` data-pid="${i.pid}"`}>
        <span class="inc-ic ${i.kind}">${glyph[i.kind] || "!"}</span>
        <div style="min-width:0"><div class="t">${esc(i.title)}</div><div class="d">${esc(i.detail)}</div></div></div>`).join("")
    : '<div style="color:var(--muted);font-size:13px;margin-top:12px">Nothing notable right now — no spikes, leaks, or crash-loops detected.</div>';
  s.innerHTML = `<button class="x" aria-label="Close">×</button>
    <div class="kick">Incidents</div>
    <h3>Notable moments</h3>
    <div class="path" style="margin-bottom:2px">Auto-detected from the capture — click one to jump to it.</div>
    ${cards}`;
  $("incidents-modal").classList.add("show");
  s.querySelector(".x").onclick = closeIncidents;
  s.querySelectorAll(".inc-card[data-pid]").forEach((el) => {
    el.onclick = () => { closeIncidents(); focusPid(+el.getAttribute("data-pid")); ensurePanelOpen(); };
  });
  renderAlerts(s);  // watches panel below the incident cards
}
function closeIncidents() { $("incidents-modal").classList.remove("show"); }
$("incBtn").addEventListener("click", openIncidents);
$("incidents-modal").addEventListener("click", (e) => { if (e.target === $("incidents-modal")) closeIncidents(); });

// ---- timeline rewind ----
// Format an ns timestamp as a wall clock (HH:MM:SS) for labels.
function fmtClock(ns) { return ns == null ? "—" : new Date(ns / 1e6).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function tlFrac() {  // where the handle sits: current view position within the window
  if (tlMin == null || tlMax == null || tlMax <= tlMin) return 1;
  const t = asOf == null ? tlMax : asOf;
  return Math.min(1, Math.max(0, (t - tlMin) / (tlMax - tlMin)));
}
function tlPaint(frac) {  // move the handle + fill + bubble; pure visual, no query
  const pct = (frac * 100).toFixed(3) + "%";
  $("tl-handle").style.left = pct; $("tl-fill").style.width = pct;
  const ts = tlMin + frac * (tlMax - tlMin);
  $("tl-bubble").textContent = frac >= LIVE_EDGE ? "now" : fmtClock(ts);
}
function fetchWindow() {
  return fetch("/api/window").then((r) => r.json()).then((w) => {
    if (w.min_ts == null || w.max_ts == null || w.max_ts <= w.min_ts) { $("timeline").classList.add("hidden"); return; }
    tlMin = w.min_ts; tlMax = Math.max(w.max_ts, lastTs || 0);
    $("timeline").classList.remove("hidden");
    $("tl-start").textContent = fmtClock(tlMin);
    if (asOf == null) { $("tl-end").textContent = "now"; tlPaint(1); }
  }).catch(() => {});
}
const LIVE_EDGE = 0.995;   // dragging past here == snap to live (asOf = null)
let tlDebounce = 0;
function commitAsOf(frac) {  // debounced: run the actual re-query at the settled position
  clearTimeout(tlDebounce);
  tlDebounce = setTimeout(async () => {
    if (frac >= LIVE_EDGE) { snapLive(); return; }
    asOf = Math.round(tlMin + frac * (tlMax - tlMin));
    updateLiveLabel();
    $("tl-track").classList.add("loading");
    try { await refreshData(); } finally { $("tl-track").classList.remove("loading"); }
  }, 200);
}
function snapLive() {  // return to now: clear as_of, resume the live poll
  asOf = null; clearTimeout(tlDebounce);
  $("timeline").classList.remove("rewound"); $("tl-track").classList.remove("loading");
  $("tl-end").textContent = "now"; tlPaint(1);
  updateLiveLabel();
  refreshData();  // one immediate live read so the view snaps forward without waiting for the tick
}
(function wireTimeline() {
  const track = $("tl-track");
  let dragging = false;
  const fracFromX = (clientX) => {
    const r = track.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  };
  const onMove = (clientX) => {
    const f = fracFromX(clientX);
    tlPaint(f);
    $("timeline").classList.toggle("rewound", f < LIVE_EDGE);
    commitAsOf(f);  // debounced — the query only fires 200ms after the handle settles
  };
  track.addEventListener("pointerdown", (e) => {
    if (tlMin == null) return;
    dragging = true; track.classList.add("drag"); track.setPointerCapture(e.pointerId);
    onMove(e.clientX); e.preventDefault();
  });
  track.addEventListener("pointermove", (e) => { if (dragging) onMove(e.clientX); });
  const end = (e) => { if (!dragging) return; dragging = false; track.classList.remove("drag"); try { track.releasePointerCapture(e.pointerId); } catch (_) {} };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);
  $("tlLive").addEventListener("click", snapLive);
})();

// ---- shareable report (v1.3a) ----
// Build a self-contained HTML file from the CURRENT view (honoring as_of) and download it.
// Everything is inlined — the graph is a PNG data URI from cytoscape — so it opens offline
// anywhere, matching the app's no-external-refs guarantee.
function reportStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
function buildReport() {
  const s = statusSummary();
  const when = asOf != null ? `rewound — as of ${fmtClock(asOf)}` : "live — now";
  const live = allProcs.filter(alive);
  const top = live.slice().sort((a, b) => curCpu(b) - curCpu(a)).slice(0, 15);
  const rows = top.map((n) => `<tr><td>${esc(base(n.exe))}</td><td class="n">${n.pid}</td><td class="n${isHot(curCpu(n)) ? " hot" : ""}">${curCpu(n).toFixed(1)}%</td><td class="n">${humanBytes(curMem(n))}</td></tr>`).join("");
  const incLabel = { cpu: "CPU", leak: "MEM", crashloop: "LOOP" };
  const incHtml = incidentList.length
    ? incidentList.map((i) => `<li class="inc ${i.kind}"><span class="tag">${incLabel[i.kind] || "!"}</span><div><b>${esc(i.title)}</b><span class="d">${esc(i.detail)}</span></div></li>`).join("")
    : `<li class="none">No spikes, leaks, or crash-loops detected.</li>`;
  let focusHtml = "";
  const fid = (selectedId && nodeById[selectedId] && nodeById[selectedId].kind === "process") ? selectedId : (graphData && graphData.culprit);
  const fn = fid && nodeById[fid];
  if (fn && fn.pid != null) {
    const cmd = (fn.args && fn.args.length) ? fn.args.join(" ") : (fn.exe || "—");
    const chain = ancestryChain(fn.id).map((id) => nodeById[id]).filter((x) => x && x.pid != null);
    const lineage = chain.map((x) => `<span class="chip${x.id === fn.id ? " self" : ""}">${esc(base(x.exe))} ${x.pid}</span>`).join('<span class="arr">→</span>');
    focusHtml = `<h2>Focused process</h2><div class="focus"><div class="fn">${esc(base(fn.exe))} <span class="fp">pid ${fn.pid}</span></div>
      <div class="cmd">${esc(cmd)}</div><div class="lin">${lineage || '<span class="muted">no captured parent</span>'}</div></div>`;
  }
  let img = "";
  try { if (cy && cy.elements().length) img = `<h2>Causal graph</h2><img class="graph" alt="causal graph" src="${cy.png({ full: true, scale: 1.5, bg: "#0b0f17" })}">`; } catch (_) {}
  const db = $("dbname").textContent || "capture";
  const gen = new Date().toLocaleString();
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CauseGraph report — ${esc(db)} — ${esc(when)}</title>
<style>
  :root{--bg:#f6f8fb;--card:#fff;--ink:#0f1622;--mut:#5b6472;--line:#e2e7ee;--acc:#1f7bb8;--hot:#d64541;--warn:#b7791f}
  *{box-sizing:border-box}body{margin:0;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg);padding:32px}
  .wrap{max-width:860px;margin:0 auto}
  header{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap;border-bottom:2px solid var(--line);padding-bottom:14px;margin-bottom:22px}
  h1{font-size:19px;margin:0;letter-spacing:.2px}.sub{color:var(--mut);font-size:12.5px}
  .badge{font:600 12px ui-monospace,monospace;color:var(--acc);background:rgba(31,123,184,.09);border:1px solid rgba(31,123,184,.25);border-radius:7px;padding:3px 9px}
  .summary{font-size:16px;font-weight:600;margin:0 0 26px;padding:14px 16px;background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:9px}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:var(--mut);margin:28px 0 10px}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:9px;overflow:hidden}
  th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);font-size:13px}
  th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);background:#fafbfd}
  tr:last-child td{border-bottom:none}.n{text-align:right;font-variant-numeric:tabular-nums;font-family:ui-monospace,monospace}.hot{color:var(--hot);font-weight:700}
  ul.inc{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px}
  .inc li{display:flex;gap:12px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:9px;padding:11px 13px}
  .inc .tag{flex:none;font:700 10px ui-monospace,monospace;padding:3px 7px;border-radius:6px;letter-spacing:.04em}
  .inc.cpu .tag{color:var(--hot);background:rgba(214,69,65,.1)}.inc.leak .tag{color:var(--acc);background:rgba(31,123,184,.1)}.inc.crashloop .tag{color:var(--warn);background:rgba(183,121,31,.12)}
  .inc b{font-weight:650}.inc .d{display:block;color:var(--mut);font-size:12.5px;margin-top:2px}.inc .none{color:var(--mut);justify-content:center}
  .focus{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:14px 16px}
  .fn{font-weight:650;font-size:15px}.fp{color:var(--mut);font-weight:500;font-size:12.5px}
  .cmd{font:12.5px ui-monospace,monospace;color:#243244;background:#f2f5f9;border:1px solid var(--line);border-radius:7px;padding:8px 10px;margin:10px 0;word-break:break-all}
  .lin{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
  .chip{font:12px ui-monospace,monospace;background:#eef2f7;border:1px solid var(--line);border-radius:6px;padding:2px 8px}.chip.self{background:rgba(31,123,184,.12);border-color:rgba(31,123,184,.3);color:var(--acc);font-weight:600}
  .arr{color:var(--mut)}.muted{color:var(--mut)}
  img.graph{max-width:100%;border:1px solid var(--line);border-radius:10px;display:block}
  footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);color:var(--mut);font-size:11.5px}
</style></head><body><div class="wrap">
  <header><div><h1>CauseGraph report</h1><div class="sub">${esc(db)} · generated ${esc(gen)}</div></div><span class="badge">${esc(when)}</span></header>
  <p class="summary">${esc(s.text)}</p>
  <h2>Top processes by CPU</h2>
  <table><thead><tr><th>Process</th><th class="n">PID</th><th class="n">CPU</th><th class="n">Memory</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">Nothing running.</td></tr>'}</tbody></table>
  <h2>Incidents (${incidentList.length})</h2>
  <ul class="inc">${incHtml}</ul>
  ${focusHtml}
  ${img}
  <footer>Generated locally by CauseGraph. This file is self-contained and contains no external references — it opens offline anywhere. It reflects the capture at the moment shown above, not live data.</footer>
</div></body></html>`;
}
function exportReport() {
  try {
    const html = buildReport();
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `causegraph-report-${reportStamp()}.html`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast("Report exported", "ok");
  } catch (e) { toast("Couldn't build the report", "err"); }
}
$("exportBtn").addEventListener("click", exportReport);

// ---- alerts / watches (v1.3b) ----
// Rules live in localStorage; evaluation is client-side on each poll. A rule fires ONCE
// when it crosses its threshold (tracked by rule.active) — not repeatedly every 4s.
const AL_KEY = "cg.alerts.rules";
let alertRules = (() => { try { return JSON.parse(localStorage.getItem(AL_KEY) || "[]"); } catch (_) { return []; } })();
let alertLog = [];  // in-memory session log of what fired, newest first
function saveRules() { try { localStorage.setItem(AL_KEY, JSON.stringify(alertRules.map(({ active, ...r }) => r))); } catch (_) {} }
const alUnit = (m) => (m === "cpu" ? "%" : " MiB");
const alLabel = (r) => `${r.metric === "cpu" ? "CPU" : "Memory"} ≥ ${r.value}${alUnit(r.metric)}`;
// Roll up alive processes by app -> {cpu total, mem MiB total}; the same grouping the list shows.
function appRollups() {
  const g = Object.create(null);  // null proto (see statusSummary)
  for (const n of allProcs.filter(alive)) { const k = appOf(n.exe); const e = g[k] || (g[k] = { cpu: 0, mem: 0 }); e.cpu += curCpu(n); e.mem += mib(curMem(n)); }
  return g;
}
// prime=true just arms the active flags to the current state without firing — used on the
// first evaluation after (re)load, so a rule that's ALREADY above its threshold doesn't fire
// a spurious alert on every page reload (it fires only on a fresh rising edge afterwards).
function evaluateAlerts(prime) {
  if (!alertRules.length || asOf != null) return;  // don't fire on rewound/past data
  const roll = appRollups();
  for (const r of alertRules) {
    const entries = r.app ? (roll[r.app] ? [[r.app, roll[r.app]]] : []) : Object.entries(roll);
    let hit = null;  // the highest-crossing app for this rule
    for (const [app, e] of entries) { const v = r.metric === "cpu" ? e.cpu : e.mem; if (v >= r.value && (!hit || v > hit.v)) hit = { app, v }; }
    if (hit && !r.active) {  // rising edge -> fire once (unless we're just priming)
      r.active = true;
      if (prime) continue;
      const val = r.metric === "cpu" ? `${hit.v.toFixed(0)}%` : `${humanBytes(hit.v * 1048576)}`;
      const text = `${hit.app} — ${r.metric === "cpu" ? "CPU" : "memory"} ${val} (≥ ${r.value}${alUnit(r.metric)})`;
      alertLog.unshift({ t: fmtClock((lastTs != null ? lastTs : 0)), text });
      if (alertLog.length > 30) alertLog.pop();
      toast(`⚠ ${text}`);
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        try { new Notification("CauseGraph alert", { body: text }); } catch (_) {}
      }
      if ($("incidents-modal").classList.contains("show")) renderAlerts($("incidents-sheet"));
    } else if (!hit && r.active) {
      r.active = false;  // dropped back below — armed to fire again next time it crosses
    }
  }
}
function alertsSectionHTML() {
  const notif = (typeof Notification !== "undefined")
    ? (Notification.permission === "granted"
        ? '<div class="al-note" style="color:var(--muted)">System notifications on.</div>'
        : Notification.permission === "default"
          ? '<div class="al-note">Want a system pop-up too? <a id="al-notify">Enable notifications</a></div>' : "")
    : "";
  const rules = alertRules.length
    ? alertRules.map((r) => `<div class="al-rule${r.active ? " on" : ""}"><b>${esc(alLabel(r))}</b>${r.app ? `<span class="for">· ${esc(r.app)}</span>` : ""}<button class="rm" data-id="${r.id}" title="Remove">×</button></div>`).join("")
    : '<div class="al-empty">No watches yet. Add one above — e.g. CPU ≥ 80% — and CauseGraph tells you when it happens.</div>';
  const log = alertLog.length
    ? `<div class="al-log"><div class="kick">Recently fired</div>${alertLog.map((l) => `<div class="al-log-item"><span class="t">${esc(l.t)}</span><span>${esc(l.text)}</span></div>`).join("")}</div>` : "";
  return `<div class="al-sec">
    <div class="kick">Alerts <span style="color:var(--dim);font-weight:500;text-transform:none;letter-spacing:0">— tell me when…</span></div>
    <div class="al-add">
      <select id="al-metric"><option value="cpu">CPU %</option><option value="mem">Memory MiB</option></select>
      <span class="op">≥</span>
      <input id="al-value" type="number" min="0" step="1" value="80">
      <input id="al-app" type="text" placeholder="any app (optional)">
      <button class="al-btn" id="al-add">Add watch</button>
    </div>
    ${rules}${log}${notif}</div>`;
}
function renderAlerts(sheet) {
  // Preserve a half-typed watch across a re-render (e.g. an unrelated alert fires while the user
  // is typing a threshold): capture the add-watch inputs + focus, restore them after rebuild.
  const prev = sheet.querySelector(".al-sec") ? {
    metric: sheet.querySelector("#al-metric") && sheet.querySelector("#al-metric").value,
    value: sheet.querySelector("#al-value") && sheet.querySelector("#al-value").value,
    app: sheet.querySelector("#al-app") && sheet.querySelector("#al-app").value,
    focus: document.activeElement && document.activeElement.closest(".al-add") ? document.activeElement.id : null,
  } : null;
  let host = sheet.querySelector(".al-sec");
  if (host) { host.outerHTML = alertsSectionHTML(); } else { sheet.insertAdjacentHTML("beforeend", alertsSectionHTML()); }
  if (prev) {
    if (prev.metric != null && sheet.querySelector("#al-metric")) sheet.querySelector("#al-metric").value = prev.metric;
    if (prev.value != null && sheet.querySelector("#al-value")) sheet.querySelector("#al-value").value = prev.value;
    if (prev.app != null && sheet.querySelector("#al-app")) sheet.querySelector("#al-app").value = prev.app;
    if (prev.focus && sheet.querySelector("#" + prev.focus)) { const el = sheet.querySelector("#" + prev.focus); el.focus(); if (el.setSelectionRange) try { el.setSelectionRange(el.value.length, el.value.length); } catch (_) {} }
  }
  wireAlerts(sheet);
}
function wireAlerts(sheet) {
  const add = sheet.querySelector("#al-add");
  if (add) add.onclick = () => {
    const metric = sheet.querySelector("#al-metric").value;
    const value = parseFloat(sheet.querySelector("#al-value").value);
    const app = sheet.querySelector("#al-app").value.trim();
    if (!(value >= 0)) { toast("Enter a threshold", "err"); return; }
    alertRules.push({ id: String(Date.now()) + Math.floor((lastTs || 0) % 1000), metric, value, app, active: false });
    saveRules(); evaluateAlerts(); renderAlerts(sheet);
  };
  sheet.querySelectorAll(".al-rule .rm").forEach((b) => { b.onclick = () => { alertRules = alertRules.filter((r) => r.id !== b.dataset.id); saveRules(); renderAlerts(sheet); }; });
  const nb = sheet.querySelector("#al-notify");
  if (nb) nb.onclick = () => { Notification.requestPermission().then(() => renderAlerts(sheet)); };
}
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
  expanded = false;  // a new focus starts from the compact view
  try {
    const r = await fetch(asq(`/api/graph?pid=${pid}&family=1&min_confidence=${$("minc").value}`));
    const d = await r.json();
    if (!r.ok || d.error) { setError(d.error || `error ${r.status}`); return; }
    renderGraph(d);
    const hit = d.nodes.find((x) => x.pid === pid && x.kind === "process");
    selectNode(hit ? hit.id : d.culprit);
    if (window.innerWidth <= 660) openNav(false);  // reveal the graph after picking on a phone
  } catch (e) { setError(`request failed: ${e}`); }
}
// Our own confirm dialog (not the browser's ugly one). Resolves true/false.
function askConfirm(title, msg) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = title;
    $("confirm-msg").textContent = msg;
    const m = $("confirm");
    const done = (v) => { m.classList.remove("show"); $("confirm-yes").onclick = $("confirm-no").onclick = m.onclick = null; document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") done(false); if (e.key === "Enter") done(true); };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
    m.onclick = (e) => { if (e.target === m) done(false); };
    document.addEventListener("keydown", onKey);
    m.classList.add("show");
    $("confirm-no").focus();
  });
}

async function killPid(pid, name) {
  if (!(await askConfirm(`Kill ${name}?`, `This force-quits pid ${pid} with SIGKILL. It cannot be undone.`))) return;
  try {
    const r = await fetch(`/api/kill?pid=${pid}`, { method: "POST", headers: { "X-CauseGraph": "1" } });
    const d = await r.json();
    if (!r.ok || d.error) { toast(d.error || `kill failed (${r.status})`, "err"); return; }
    toast(`Killed ${name} (pid ${pid})`, "ok");
    setTimeout(refreshData, 500);  // refresh in place — stay on this neighborhood, don't jump
  } catch (e) { toast(`request failed: ${e}`, "err"); }
}

// ================= boot + controls =================
// Show the empty/disconnected overlay with a specific, actionable description.
function emptyState(title, html) {
  const el = $("empty");
  el.querySelector("h2").textContent = title;
  el.querySelector("p").innerHTML = html;
  overlay("empty");
  $("mstatus").className = "mstatus quiet";
  $("mstatus").innerHTML = `<span class="dotq"></span>${esc(title)}`;
}

async function loadAll() {
  overlay("loading");  // spinner up front — the first read can take a moment on a big capture
  try { const m = await (await fetch("/api/meta")).json(); if (m.db) { $("dbname").textContent = m.db; $("art-db").textContent = m.db; } if (m.latest_ts != null) lastTs = m.latest_ts; } catch (_) {}
  let d;
  try {
    const r = await fetch(asq("/api/graph?all=1&max_nodes=20000"));  // the list wants every process, not the graph cap
    d = await r.json();
    if (!r.ok || d.error) throw new Error(d && d.error);
  } catch (e) {
    emptyState("Can't read the capture", "The recorder's database couldn't be read. Is it running? Check with <code>cg status</code>, or start it with <code>cg up</code>.");
    return;
  }
  allProcs = (d.nodes || []).filter((n) => n.kind === "process");
  $("art-sub").textContent = `sqlite · ${allProcs.length} processes`;
  renderList();
  fetchIncidents();  // lazy, non-blocking — fills the header badge after first paint
  fetchWindow();     // populate the timeline scrubber range
  evaluateAlerts(true);  // prime watches to current state (no spurious fire on reload)
  const live = allProcs.filter(alive);
  if (!allProcs.length) { emptyState("Nothing captured yet", "The recorder hasn't written any events. Start it with <code>cg up</code> — then this fills in within a few seconds."); return; }
  if (!live.length) { emptyState("Recorder looks stopped", "The capture has history but nothing was sampled recently — the recorder isn't writing. Restart it with <code>cg up</code>."); return; }
  // default view: the hottest LIVE process that has a real tree, so we land on something meaningful
  const cands = live.sort((a, b) => curCpu(b) - curCpu(a)).slice(0, 6);
  for (const c of cands) {
    try {
      const r = await fetch(asq(`/api/graph?pid=${c.pid}&family=1&min_confidence=0.5`));
      const g = await r.json();
      if (r.ok && !g.error && g.nodes.length > 1) { currentPid = c.pid; renderGraph(g); const hit = g.nodes.find((x) => x.pid === c.pid && x.kind === "process"); selectNode(hit ? hit.id : g.culprit); return; }
    } catch (_) {}
  }
  focusPid(cands[0].pid);  // fallback: hottest (may be a lone node)
}

// Search is a pure filter. Enter focuses the top match: a number -> that pid;
// text -> the highest-ranked process currently matching the filter.
$("f").addEventListener("submit", (e) => {
  e.preventDefault(); const v = $("query").value.trim(); if (!v) return;
  if (/^\d+$/.test(v)) { focusPid(parseInt(v, 10)); return; }
  const top = allProcs.filter(alive).filter(matchesFilter).sort((a, b) => curCpu(b) - curCpu(a))[0];
  if (top) focusPid(top.pid);
});
$("query").addEventListener("input", (e) => { filterText = e.target.value.trim(); renderList(); applyGraphFilter(); });
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; sortMode = b.dataset.sort; [...e.currentTarget.children].forEach((c) => c.classList.toggle("on", c === b)); renderList(); });
$("gtabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; groupMode = b.dataset.group; [...e.currentTarget.children].forEach((c) => c.classList.toggle("on", c === b)); renderList(); });
// Changing the confidence threshold must re-query the graph NOW (not silently wait for the
// next poll) — otherwise the control feels dead. Re-centre the current family with the new floor.
$("minc").addEventListener("change", () => { if (currentPid != null) focusPid(currentPid); });
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
// Clicking a process should always reveal its details — so if the inspector was
// collapsed, open it (otherwise the click looks like it did nothing).
function ensurePanelOpen() {
  const m = document.querySelector("main");
  if (!m.classList.contains("rcollapsed")) return;
  m.classList.remove("rcollapsed");
  $("panelToggle").title = "Hide inspector";
  syncFor(500);
  setTimeout(() => { cy.resize(); refit(); }, 210);
}

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
    || asOf != null    // rewound: viewing the past, so the live poll is paused
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
    card.classList.toggle("hot", n.kind === "process" && isHot(curCpu(n)));
  }
  applyGraphFilter();
  setStatus(d);
  if (selectedId && nodeById[selectedId]) selectNode(selectedId);  // refresh inspector numbers
}

async function refreshData() {
  refreshing = true;
  const sc = $("plist").scrollTop;
  try {
    const rl = await fetch(asq("/api/graph?all=1&max_nodes=20000"));
    const dl = await rl.json();
    if (rl.ok && !dl.error) {
      allProcs = (dl.nodes || []).filter((n) => n.kind === "process");
      $("art-sub").textContent = `sqlite · ${allProcs.length} processes${dl.truncated ? "+" : ""}`;
      renderList();
      evaluateAlerts();  // re-check watches against the fresh roll-ups each poll
    }
    if (currentPid != null) {
      const rg = await fetch(asq(`/api/graph?pid=${currentPid}&family=1${expanded ? "&expand=1" : ""}&min_confidence=${$("minc").value}`));
      const dg = await rg.json();
      if (rg.ok && !dg.error) applyGraphUpdate(dg);
    }
    try { noteTs((await (await fetch("/api/meta")).json()).latest_ts); } catch (_) {}
    fetchIncidents();  // keep the badge current with the poll
  } catch (_) { /* transient; next tick retries */ }
  finally { refreshing = false; $("plist").scrollTop = sc; }
}

function autoTick() { if (!busy()) refreshData(); }  // skip this beat if mid-interaction

// Did the newest event move since last poll? If not for 2 polls, the recorder stopped.
function noteTs(ts) {
  if (ts != null && (lastTs == null || ts > lastTs)) { lastTs = ts; staleCount = 0; frozen = false; }
  else if (++staleCount >= 2) frozen = true;
  // the capture keeps growing; the live edge of the timeline tracks the newest event
  if (lastTs != null && tlMax != null && lastTs > tlMax) { tlMax = lastTs; if (asOf == null) tlPaint(1); }
  updateLiveLabel();
}
function updateLiveLabel() {
  const chip = document.querySelector(".dbchip"), live = $("live");
  if (asOf != null) {  // rewound — the poll is paused; the header says so, the Live button snaps back
    live.textContent = `⏸ rewound · ${fmtClock(asOf)}`; live.className = "livebtn frozen";
    live.title = "Viewing the past — auto-refresh paused. Click Live (or the timeline's Live button) to return to now.";
    chip.classList.add("paused"); chip.classList.remove("frozen");
    return;
  }
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
$("live").addEventListener("click", () => {
  if (asOf != null) { snapLive(); return; }  // rewound -> the button returns to now
  setAuto(AUTO_STEPS[(AUTO_STEPS.indexOf(autoMs) + 1) % AUTO_STEPS.length]);
});

// ---- help popover engine ----
// One shared popover, shown on hover/focus of ANY element carrying data-help (the small "i"
// markers, and a few icon buttons). Clamped to the viewport; flips above if it'd overflow.
(function helpSystem() {
  const pop = document.createElement("div"); pop.id = "help-pop"; document.body.appendChild(pop);
  let hideT = 0, cur = null;
  function show(el) {
    const txt = el.getAttribute("data-help"); if (!txt) return;
    clearTimeout(hideT); cur = el;
    pop.innerHTML = txt; pop.classList.add("show");
    const r = el.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight, m = 8;
    let left = Math.max(m, Math.min(r.left + r.width / 2 - pw / 2, window.innerWidth - pw - m));
    let top = r.bottom + 8;
    if (top + ph > window.innerHeight - m) top = r.top - ph - 8;  // flip above when no room below
    pop.style.left = left + "px"; pop.style.top = Math.max(m, top) + "px";
  }
  function hide(el) { if (el && el !== cur) return; hideT = setTimeout(() => { pop.classList.remove("show"); cur = null; }, 60); }
  hideHelp = () => { clearTimeout(hideT); pop.classList.remove("show"); cur = null; };  // hide now (on re-render)
  const near = (e) => (e.target.closest ? e.target.closest("[data-help]") : null);
  document.addEventListener("mouseover", (e) => { const el = near(e); if (el) show(el); });
  document.addEventListener("mouseout", (e) => { const el = near(e); if (el) hide(el); });
  document.addEventListener("focusin", (e) => { const el = near(e); if (el) show(el); });
  document.addEventListener("focusout", (e) => { const el = near(e); if (el) hide(el); });
  document.addEventListener("click", (e) => { if (!near(e)) hideHelp(); });  // any real click dismisses it
  window.addEventListener("scroll", () => hideHelp(), true);
})();

loadAll();
setAuto(autoMs);  // start the live poll (first tick one interval from now)
