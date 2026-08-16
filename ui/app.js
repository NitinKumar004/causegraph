/* CauseGraph UI — renders the causal graph from the local API with cytoscape.js.
   No reasoning here; the server already computed the graph. */
"use strict";

const $ = (id) => document.getElementById(id);

// palette (validated data-viz palette, dark)
const C = {
  blue: "#3f8ae8", amber: "#e0a133", spawn: "#6a7180",
  good: "#2fa34a", warn: "#f0a83c", hot: "#e5544f",
  ink: "#f5f5f4", muted: "#83837c", culprit: "#cfe0ff",
};
const heat = (cpu) => cpu == null ? C.blue : cpu < 15 ? C.good : cpu < 50 ? C.warn : C.hot;
const base = (p) => (p || "").split("/").filter(Boolean).pop() || p || "?";
function humanBytes(n) {
  if (n == null) return "—";
  let v = n, i = 0; const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

const cy = cytoscape({
  container: $("cy"),
  minZoom: 0.2, maxZoom: 1.6, wheelSensitivity: 0.22,
  style: [
    { selector: "node", style: {
        "label": "data(label)", "font-size": 12.5, "font-weight": 600, "line-height": 1.25,
        "color": C.ink, "text-wrap": "wrap", "text-max-width": 150,
        "text-valign": "center", "text-halign": "center",
        "text-outline-width": 2.4, "text-outline-color": "#0d0f13", "text-outline-opacity": 0.9,
        "width": "label", "height": "label", "padding": "14px", "shape": "round-rectangle",
        "border-width": 1.6, "border-color": "data(border)",
        "background-fill": "linear-gradient", "background-gradient-direction": "to-bottom",
        "transition-property": "opacity, border-width, underlay-opacity",
        "transition-duration": "140ms" } },
    { selector: 'node[kind="process"]', style: { "background-gradient-stop-colors": "#212c3c #161f2b" } },
    { selector: 'node[kind="file"]', style: { "background-gradient-stop-colors": "#332a18 #241d0f" } },
    { selector: "node.culprit", style: {
        "border-width": 2.6, "border-color": C.culprit, "font-size": 13.5,
        "underlay-color": C.blue, "underlay-opacity": 0.22, "underlay-padding": 12 } },
    { selector: "node:selected", style: {
        "border-width": 2.8, "border-color": "#eaf1ff",
        "underlay-color": "#7fb0f5", "underlay-opacity": 0.28, "underlay-padding": 12 } },
    { selector: "edge", style: {
        "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 1.05,
        "width": "data(w)", "line-color": "data(col)", "target-arrow-color": "data(col)",
        "line-style": "data(style)", "opacity": 0.92,
        "label": "data(elabel)", "font-size": 10, "font-weight": 600, "color": "#d8d7cf",
        "text-rotation": "autorotate", "text-background-color": "#111318",
        "text-background-opacity": 0.9, "text-background-padding": 3, "text-background-shape": "round-rectangle",
        "transition-property": "opacity", "transition-duration": "140ms" } },
    { selector: ".dim", style: { "opacity": 0.12 } },
    { selector: ".hl", style: { "opacity": 1 } },
  ],
});

function overlay(which) {
  for (const id of ["empty", "loading", "error"]) $(id).classList.toggle("show", id === which);
}
function setError(msg) { $("error-msg").textContent = msg; overlay("error"); }

function nodeEls(n, culprit) {
  const isProc = n.kind === "process";
  const title = base(isProc ? n.exe : n.path);
  return {
    data: {
      id: n.id, kind: n.kind, meta: n,
      label: isProc ? `${title}\npid ${n.pid}` : title,
      border: isProc ? heat(n.peak_cpu_pct) : C.amber,
    },
    classes: n.id === culprit ? "culprit" : "",
  };
}

function render(data) {
  const els = [];
  for (const n of data.nodes) els.push(nodeEls(n, data.culprit));
  for (const e of data.edges) {
    const fw = e.rule === "file_watch";
    els.push({ data: {
      id: `${e.source}->${e.target}`, source: e.source, target: e.target,
      elabel: fw && e.confidence != null ? `${e.confidence.toFixed(2)}` : "",
      col: fw ? (e.confidence >= 0.75 ? "#f0b24a" : "#d3922c") : C.spawn,
      w: fw ? (e.confidence == null ? 2 : 1.6 + e.confidence * 2.8) : 2.4,
      style: fw ? "dashed" : "solid",
    }});
  }
  cy.elements().remove();
  cy.add(els);
  const layout = cy.layout({
    name: "breadthfirst", directed: true, padding: 30, spacingFactor: 1.05,
    avoidOverlap: true, animate: true, animationDuration: 420, animationEasing: "ease-out",
  });
  layout.one("layoutstop", () => cy.animate({ fit: { padding: 80 }, duration: 320, easing: "ease-out" }));
  layout.run();
  overlay(null);

  const cn = cy.$id(data.culprit);
  if (cn.nonempty()) { cn.select(); showDetails(cn); }
  const c = $("count");
  c.innerHTML = `<b>${data.nodes.length}</b> node${data.nodes.length === 1 ? "" : "s"}` +
                (data.truncated ? " · truncated" : "");
  c.className = data.truncated ? "count warn" : "count";
}

// details panel
function showDetails(node) {
  const n = node.data("meta"); if (!n) return;
  if (n.kind === "process") {
    const cpu = n.peak_cpu_pct, pct = cpu == null ? 0 : Math.min(100, cpu);
    $("p-title").textContent = base(n.exe);
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
    $("p-title").textContent = base(n.path);
    $("p-sub").textContent = n.path || "";
    $("p-body").innerHTML = `<span class="tag2">file</span>
      <p class="hint" style="margin-top:11px">A change to this file likely triggered a process below
      (a <code>file_watch</code> edge). Confidence is shown on the edge.</p>`;
  }
}
function clearDetails() {
  $("p-title").textContent = "Nothing selected";
  $("p-sub").textContent = "Click a node to inspect it.";
  $("p-body").innerHTML = '<span class="hint">Click any process or file to see its details.</span>';
}

cy.on("mouseover", "node", (e) => { cy.elements().addClass("dim"); e.target.closedNeighborhood().removeClass("dim").addClass("hl"); });
cy.on("mouseout", "node", () => cy.elements().removeClass("dim hl"));
cy.on("tap", "node", (e) => showDetails(e.target));
cy.on("tap", (e) => { if (e.target === cy) { clearDetails(); cy.$(":selected").unselect(); } });

async function ask() {
  const pid = $("pid").value.trim(), q = $("q").value.trim(), minc = $("minc").value;
  const params = new URLSearchParams();
  if (pid) params.set("pid", pid); else if (q) params.set("q", q); else { $("pid").focus(); return; }
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

// On open: show the loaded DB and auto-trace the hottest process.
async function init() {
  try { const m = await (await fetch("/api/meta")).json(); if (m.db) $("dbname").textContent = m.db; } catch (_) {}
  try {
    overlay("loading");
    const res = await fetch("/api/graph?q=cpu&min_confidence=0.5");
    const data = await res.json();
    if (res.ok && !data.error && data.nodes && data.nodes.length) render(data); else overlay("empty");
  } catch (_) { overlay("empty"); }
}
init();
