/* CauseGraph UI — renders the causal graph from the local API with cytoscape.js.
   No reasoning here; the server already computed the graph. */
"use strict";

const $ = (id) => document.getElementById(id);

// --- palette (from the validated data-viz palette, dark mode) ---
const C = {
  procBg: "#1b2431", fileBg: "#2c2415",
  blue: "#3f8ae8", amber: "#e0a133",
  good: "#2fa34a", warn: "#f0a83c", hot: "#e5544f",
  spawnEdge: "#5f6672", ink: "#f4f4f3", muted: "#86867f",
  culprit: "#93c0ff",
};

function heat(cpu) {                       // process border by CPU load
  if (cpu == null) return C.blue;
  if (cpu < 15) return C.good;
  if (cpu < 50) return C.warn;
  return C.hot;
}
function edgeColor(rule, conf) {
  if (rule === "spawn") return C.spawnEdge;
  // file_watch: amber, brightening with confidence
  const t = conf == null ? 0.5 : conf;
  return t >= 0.75 ? "#f0b24a" : t >= 0.5 ? "#d9962e" : "#a8752a";
}
function humanBytes(n) {
  if (n == null) return "—";
  let v = n; const u = ["B", "KiB", "MiB", "GiB", "TiB"]; let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

const cy = cytoscape({
  container: $("cy"),
  wheelSensitivity: 0.25,
  style: [
    { selector: "node", style: {
        "label": "data(label)", "font-size": 11, "font-weight": 600,
        "color": C.ink, "text-wrap": "wrap", "text-max-width": 150,
        "text-valign": "center", "text-halign": "center",
        "background-color": "data(bg)", "border-width": 1.6, "border-color": "data(border)",
        "width": "label", "height": "label", "padding": "10px",
        "shape": "round-rectangle", "text-margin-y": 0,
        "transition-property": "opacity, border-width", "transition-duration": "120ms" } },
    { selector: 'node[kind="file"]', style: {
        "shape": "round-rectangle", "background-color": C.fileBg, "font-weight": 500,
        "text-max-width": 130 } },
    { selector: "node.culprit", style: {
        "border-width": 3, "border-color": C.culprit } },
    { selector: "node:selected", style: {
        "border-width": 3, "border-color": "#cfe2ff" } },
    { selector: "edge", style: {
        "curve-style": "bezier", "target-arrow-shape": "triangle",
        "width": "data(w)", "line-color": "data(col)", "target-arrow-color": "data(col)",
        "line-style": "data(style)", "arrow-scale": 0.9, "opacity": 0.9,
        "label": "data(elabel)", "font-size": 8.5, "color": C.muted,
        "text-rotation": "autorotate", "text-background-color": C.procBg,
        "text-background-opacity": 0.85, "text-background-padding": 2,
        "transition-property": "opacity", "transition-duration": "120ms" } },
    { selector: ".dim", style: { "opacity": 0.12 } },
    { selector: ".hl", style: { "opacity": 1 } },
  ],
});

// --- overlays / states ---
function overlay(which) {
  for (const id of ["empty", "loading", "error"]) $(id).classList.toggle("show", id === which);
}
function setError(msg) { $("error-msg").textContent = msg; overlay("error"); }

// --- render ---
function render(data) {
  const els = [];
  const culprit = data.culprit;
  for (const n of data.nodes) {
    const isProc = n.kind === "process";
    els.push({ data: {
      id: n.id, label: n.label, kind: n.kind, meta: n,
      bg: isProc ? C.procBg : C.fileBg,
      border: isProc ? heat(n.peak_cpu_pct) : C.amber,
    }, classes: n.id === culprit ? "culprit" : "" });
  }
  for (const e of data.edges) {
    const label = e.rule === "spawn" ? "spawn" : `${e.rule} ${e.confidence != null ? e.confidence.toFixed(2) : ""}`;
    els.push({ data: {
      id: `${e.source}->${e.target}`, source: e.source, target: e.target,
      elabel: label, col: edgeColor(e.rule, e.confidence),
      w: e.confidence == null ? 2 : 1.4 + e.confidence * 2.6,
      style: e.rule === "spawn" ? "solid" : "dashed",
    }});
  }
  cy.elements().remove();
  cy.add(els);
  cy.layout({ name: "breadthfirst", directed: true, padding: 46, spacingFactor: 1.3,
              animate: true, animationDuration: 350 }).run();
  overlay(null);
  const cn = cy.$id(culprit);
  if (cn.nonempty()) { cn.select(); showDetails(cn); }
  const c = $("count");
  c.innerHTML = `<b>${data.nodes.length}</b> node${data.nodes.length === 1 ? "" : "s"}` +
                (data.truncated ? " · truncated" : "");
  c.className = data.truncated ? "hstatus warn" : "hstatus";
}

// --- details panel ---
function showDetails(node) {
  const n = node.data("meta");
  if (!n) return;
  if (n.kind === "process") {
    const cpu = n.peak_cpu_pct;
    const pct = cpu == null ? 0 : Math.min(100, cpu);
    $("p-title").textContent = (n.exe || "?").split("/").pop() || n.exe || "?";
    $("p-sub").textContent = n.exe || "";
    $("p-body").innerHTML = `
      <dl class="kv">
        <dt>pid</dt><dd>${n.pid}</dd>
        <dt>user</dt><dd>${n.user || "—"}</dd>
        <dt>started</dt><dd>${n.observed_spawn ? "observed" : "inferred (pre-capture)"}</dd>
        <dt>peak CPU</dt><dd>${cpu == null ? "—" : cpu.toFixed(1) + "%"}</dd>
      </dl>
      <div class="meter"><span style="width:${pct}%;background:${heat(cpu)}"></span></div>
      <div class="divider"></div>
      <dl class="kv"><dt>peak RSS</dt><dd>${humanBytes(n.peak_rss_bytes)}</dd></dl>`;
  } else {
    $("p-title").textContent = (n.path || "").split("/").pop() || n.path;
    $("p-sub").textContent = n.path || "";
    $("p-body").innerHTML = `<span class="tag">file</span>
      <p class="hint" style="margin-top:10px">A change to this file likely triggered a process below
      (a <code>file_watch</code> edge). Confidence is shown on the edge.</p>`;
  }
}
function clearDetails() {
  $("p-title").textContent = "Nothing selected";
  $("p-sub").textContent = "Click a node to inspect it.";
  $("p-body").innerHTML = '<span class="hint">Click any process or file to see its details.</span>';
}

// hover highlight: focus a node + its neighborhood, fade the rest
cy.on("mouseover", "node", (e) => {
  const nb = e.target.closedNeighborhood();
  cy.elements().addClass("dim");
  nb.removeClass("dim").addClass("hl");
});
cy.on("mouseout", "node", () => cy.elements().removeClass("dim hl"));
cy.on("tap", "node", (e) => showDetails(e.target));
cy.on("tap", (e) => { if (e.target === cy) { clearDetails(); cy.$(":selected").unselect(); } });

// --- query ---
async function ask() {
  const pid = $("pid").value.trim(), q = $("q").value.trim(), minc = $("minc").value;
  const params = new URLSearchParams();
  if (pid) params.set("pid", pid);
  else if (q) params.set("q", q);
  else { $("pid").focus(); return; }
  params.set("min_confidence", minc);
  overlay("loading");
  try {
    const res = await fetch(`/api/graph?${params}`);
    const data = await res.json();
    if (!res.ok || data.error) { setError(data.error || `error ${res.status}`); return; }
    render(data);
  } catch (err) { setError(`request failed: ${err}`); }
}

$("f").addEventListener("submit", (e) => { e.preventDefault(); ask(); });

// On open: show which DB is loaded, and auto-trace the hottest process so the
// viewer lands on a real graph instead of a blank canvas.
async function init() {
  try {
    const m = await (await fetch("/api/meta")).json();
    if (m.db) $("dbname").textContent = m.db;
  } catch (_) { /* leave default */ }
  try {
    overlay("loading");
    const res = await fetch("/api/graph?q=cpu&min_confidence=0.5");
    const data = await res.json();
    if (res.ok && !data.error && data.nodes && data.nodes.length) render(data);
    else overlay("empty");
  } catch (_) { overlay("empty"); }
}
init();
