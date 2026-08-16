/* CauseGraph UI — renders the causal graph from the local API with cytoscape.js.
   No reasoning here; the server already computed the graph. */
"use strict";

const $ = (id) => document.getElementById(id);
const C = { blue: "#4c94ec", amber: "#d9a441", good: "#4ba869", warn: "#e0a83c", hot: "#e0625d", muted: "#6f727a" };
const heat = (cpu) => cpu == null ? C.muted : cpu < 15 ? C.good : cpu < 50 ? C.warn : C.hot;
const base = (p) => (p || "").split("/").filter(Boolean).pop() || p || "?";
function humanBytes(n) {
  if (n == null) return "—";
  let v = n, i = 0; const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

const cy = cytoscape({
  container: $("cy"), minZoom: 0.25, maxZoom: 1.6, wheelSensitivity: 0.22,
  style: [
    { selector: "node", style: {
        "label": "data(label)", "font-size": 12.5, "font-weight": 600, "line-height": 1.3,
        "color": "#eef0f3", "text-wrap": "wrap", "text-max-width": 150,
        "text-valign": "center", "text-halign": "center",
        "width": "label", "height": "label", "padding": "13px", "shape": "round-rectangle",
        "border-width": 1, "border-color": "rgba(255,255,255,.11)", "background-color": "#181b22",
        "transition-property": "opacity, border-color, underlay-opacity", "transition-duration": "130ms" } },
    { selector: 'node[kind="file"]', style: { "background-color": "#201b12", "border-color": "rgba(217,164,65,.4)" } },
    { selector: "node.culprit", style: {
        "border-width": 1.6, "border-color": C.blue, "underlay-color": C.blue, "underlay-opacity": 0.14, "underlay-padding": 9 } },
    { selector: "node:selected", style: {
        "border-width": 1.6, "border-color": "#8ab8f2", "underlay-color": "#8ab8f2", "underlay-opacity": 0.18, "underlay-padding": 9 } },
    { selector: "edge", style: {
        "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.85,
        "width": "data(w)", "line-color": "data(col)", "target-arrow-color": "data(col)", "line-style": "data(style)", "opacity": 0.8,
        "label": "data(elabel)", "font-size": 9.5, "color": C.muted, "text-rotation": "autorotate",
        "text-background-color": "#0c0d10", "text-background-opacity": 0.9, "text-background-padding": 2,
        "transition-property": "opacity", "transition-duration": "130ms" } },
    { selector: ".dim", style: { "opacity": 0.1 } },
    { selector: ".hl", style: { "opacity": 1 } },
  ],
});

function overlay(which) { for (const id of ["empty", "loading", "error"]) $(id).classList.toggle("show", id === which); }
function setError(msg) { $("error-msg").textContent = msg; overlay("error"); }

function render(data) {
  const els = [];
  for (const n of data.nodes) {
    const title = base(n.kind === "process" ? n.exe : n.path);
    els.push({ data: { id: n.id, kind: n.kind, meta: n,
      label: n.kind === "process" ? `${title}\npid ${n.pid}` : title },
      classes: n.id === data.culprit ? "culprit" : "" });
  }
  for (const e of data.edges) {
    const fw = e.rule === "file_watch";
    els.push({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target,
      elabel: fw && e.confidence != null ? e.confidence.toFixed(2) : "",
      col: fw ? "#c99539" : "#565a63", w: fw ? (e.confidence == null ? 1.6 : 1.3 + e.confidence * 2) : 1.6,
      style: fw ? "dashed" : "solid" } });
  }
  cy.elements().remove(); cy.add(els);
  const layout = cy.layout({ name: "breadthfirst", directed: true, padding: 30, spacingFactor: 1.15, avoidOverlap: true, animate: true, animationDuration: 400, animationEasing: "ease-out" });
  layout.one("layoutstop", () => cy.animate({ fit: { padding: 90 }, duration: 300, easing: "ease-out" }));
  layout.run();
  overlay(null);

  const cn = cy.$id(data.culprit);
  if (cn.nonempty()) { cn.select(); showDetails(cn); }
  const c = $("count");
  c.textContent = ` · ${data.nodes.length} node${data.nodes.length === 1 ? "" : "s"}${data.truncated ? " (truncated)" : ""}`;
  c.className = data.truncated ? "warn" : "";
}

function showDetails(node) {
  const n = node.data("meta"); if (!n) return;
  if (n.kind === "process") {
    const cpu = n.peak_cpu_pct, pct = cpu == null ? 0 : Math.min(100, cpu);
    $("p-title").textContent = base(n.exe); $("p-sub").textContent = n.exe || "";
    $("p-body").innerHTML = `
      <dl class="kv">
        <dt>pid</dt><dd>${n.pid}</dd>
        <dt>user</dt><dd>${n.user || "—"}</dd>
        <dt>started</dt><dd>${n.observed_spawn ? "observed" : "inferred"}</dd>
        <dt>peak CPU</dt><dd>${cpu == null ? "—" : cpu.toFixed(1) + "%"}</dd>
      </dl>
      <div class="meter"><span style="width:${pct}%;background:${heat(cpu)}"></span></div>
      <div class="divider"></div>
      <dl class="kv"><dt>peak RSS</dt><dd>${humanBytes(n.peak_rss_bytes)}</dd></dl>`;
  } else {
    $("p-title").textContent = base(n.path); $("p-sub").textContent = n.path || "";
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

async function trace(query, minc) {
  const params = new URLSearchParams();
  if (/^\d+$/.test(query)) params.set("pid", query); else params.set("q", query);
  params.set("min_confidence", minc);
  overlay("loading");
  try {
    const res = await fetch(`/api/graph?${params}`);
    const data = await res.json();
    if (!res.ok || data.error) { setError(data.error || `error ${res.status}`); return; }
    render(data);
  } catch (err) { setError(`request failed: ${err}`); }
}
$("f").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("query").value.trim();
  if (!v) { $("query").focus(); return; }
  trace(v, $("minc").value);
});

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
