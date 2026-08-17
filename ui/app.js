/* CauseGraph UI — cytoscape lays out the graph + draws the connector curves; each
   node is a real HTML card overlaid on top (rich text: name + small pid). No
   reasoning here; the server already computed the graph. */
"use strict";

const $ = (id) => document.getElementById(id);
const base = (p) => (p || "").split("/").filter(Boolean).pop() || p || "?";
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
const heat = (cpu) => cpu == null ? "#6f727a" : cpu < 15 ? "#4ba869" : cpu < 50 ? "#e0a83c" : "#e0625d";
function humanBytes(n) {
  if (n == null) return "—";
  let v = n, i = 0; const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
}

const NODE_W = 156, NODE_H = 48;

const cy = cytoscape({
  container: $("cy"), minZoom: 0.25, maxZoom: 1.6, wheelSensitivity: 0.22,
  style: [
    // invisible placeholder — the HTML card is the visual; this drives layout + edge geometry
    { selector: "node", style: { "width": NODE_W, "height": NODE_H, "shape": "round-rectangle",
        "background-opacity": 0, "border-width": 0, "events": "no" } },
    { selector: "edge", style: {
        "curve-style": "bezier", "width": "data(w)", "line-color": "data(col)", "line-style": "data(style)", "opacity": 0.75,
        "source-arrow-shape": "circle", "source-arrow-color": "data(col)",
        "target-arrow-shape": "circle", "target-arrow-color": "data(col)", "arrow-scale": 0.42 } },
  ],
});

// ---- HTML node cards ----
const layer = $("nodes");
let cards = {};

function buildCards() {
  layer.innerHTML = ""; cards = {};
  cy.nodes().forEach((n) => {
    const m = n.data("meta"); const isProc = m.kind === "process";
    const div = document.createElement("div");
    div.className = "gnode" + (n.hasClass("culprit") ? " culprit" : "");
    div.innerHTML =
      `<div class="top"><span class="ic ${isProc ? "p" : "f"}"></span>` +
      `<span class="name">${esc(base(isProc ? m.exe : m.path))}</span></div>` +
      (isProc ? `<span class="pid">pid ${m.pid}</span>` : `<span class="pid">file</span>`);
    div.addEventListener("click", (e) => { e.stopPropagation(); selectNode(n); });
    div.addEventListener("mouseenter", () => hoverNode(n));
    div.addEventListener("mouseleave", () => unhover());
    layer.appendChild(div);
    cards[n.id()] = div;
  });
  positionCards();
}

function positionCards() {
  const z = cy.zoom();
  cy.nodes().forEach((n) => {
    const d = cards[n.id()]; if (!d) return;
    const p = n.renderedPosition();
    d.style.transform = `translate(${p.x}px, ${p.y}px) translate(-50%, -50%) scale(${z})`;
  });
}
// keep cards glued to the graph during pans, zooms and layout animation
cy.on("pan zoom", positionCards);
cy.on("position", "node", positionCards);
function syncFor(ms) { const end = performance.now() + ms; (function tick() { positionCards(); if (performance.now() < end) requestAnimationFrame(tick); })(); }

function overlay(which) { for (const id of ["empty", "loading", "error"]) $(id).classList.toggle("show", id === which); }
function setError(msg) { $("error-msg").textContent = msg; overlay("error"); }

function render(data) {
  const els = [];
  for (const n of data.nodes) els.push({ data: { id: n.id, kind: n.kind, meta: n }, classes: n.id === data.culprit ? "culprit" : "" });
  for (const e of data.edges) {
    const fw = e.rule === "file_watch";
    els.push({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target,
      conf: fw ? e.confidence : null, col: "#7f858e", w: 1.5, style: fw ? "dashed" : "solid" } });
  }
  cy.elements().remove(); cy.add(els);
  const layout = cy.layout({ name: "breadthfirst", directed: true, padding: 30, spacingFactor: 1.15, avoidOverlap: true, animate: true, animationDuration: 400, animationEasing: "ease-out" });
  layout.one("layoutstop", () => { cy.animate({ fit: { padding: 90 }, duration: 300, easing: "ease-out" }); syncFor(800); });
  layout.run();
  buildCards(); syncFor(900);
  overlay(null);

  const cn = cy.$id(data.culprit);
  if (cn.nonempty()) selectNode(cn);
  const c = $("count");
  c.textContent = ` · ${data.nodes.length} node${data.nodes.length === 1 ? "" : "s"}${data.truncated ? " (truncated)" : ""}`;
  c.className = data.truncated ? "warn" : "";
}

// ---- interaction ----
function selectNode(n) {
  cy.$(":selected").unselect(); n.select();
  for (const id in cards) cards[id].classList.toggle("sel", id === n.id());
  showDetails(n);
}
function hoverNode(n) {
  const hi = n.closedNeighborhood();
  const keep = new Set(hi.nodes().map((x) => x.id()));
  cy.edges().style("opacity", 0.07);
  hi.edges().style("opacity", 0.9);
  for (const id in cards) cards[id].classList.toggle("dim", !keep.has(id));
}
function unhover() { cy.edges().removeStyle("opacity"); for (const id in cards) cards[id].classList.remove("dim"); }
cy.on("tap", (e) => { if (e.target === cy) { cy.$(":selected").unselect(); for (const id in cards) cards[id].classList.remove("sel"); clearDetails(); } });

function showDetails(node) {
  const n = node.data("meta"); if (!n) return;
  if (n.kind === "process") {
    const cpu = n.peak_cpu_pct, pct = cpu == null ? 0 : Math.min(100, cpu);
    $("p-title").textContent = base(n.exe); $("p-sub").textContent = n.exe || "";
    $("p-body").innerHTML = `
      <dl class="kv">
        <dt>pid</dt><dd>${n.pid}</dd>
        <dt>user</dt><dd>${esc(n.user) || "—"}</dd>
        <dt>started</dt><dd>${n.observed_spawn ? "observed" : "inferred"}</dd>
        <dt>peak CPU</dt><dd>${cpu == null ? "—" : cpu.toFixed(1) + "%"}</dd>
      </dl>
      <div class="meter"><span style="width:${pct}%;background:${heat(cpu)}"></span></div>
      <div class="divider"></div>
      <dl class="kv"><dt>peak RSS</dt><dd>${humanBytes(n.peak_rss_bytes)}</dd></dl>`;
  } else {
    $("p-title").textContent = base(n.path); $("p-sub").textContent = n.path || "";
    const oe = node.outgoers("edge"); const conf = oe.nonempty() ? oe[0].data("conf") : null;
    $("p-body").innerHTML = `<span class="tag2">file</span>
      <p class="hint" style="margin-top:11px">A change to this file likely triggered the connected process
      (a <code>file_watch</code> edge)${conf != null ? ` — confidence <b style="color:#d9a441">${conf.toFixed(2)}</b>` : ""}.</p>`;
  }
}
function clearDetails() {
  $("p-title").textContent = "Nothing selected";
  $("p-sub").textContent = "Click a node to inspect it.";
  $("p-body").innerHTML = '<span class="hint">Click any process or file to see its details.</span>';
}

// ---- query ----
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
$("f").addEventListener("submit", (e) => { e.preventDefault(); const v = $("query").value.trim(); if (!v) { $("query").focus(); return; } trace(v, $("minc").value); });

async function init() {
  try { const m = await (await fetch("/api/meta")).json(); if (m.db) $("dbname").textContent = m.db; } catch (_) {}
  try {
    overlay("loading");
    // whole system tree by default; fall back to the hottest process
    let res = await fetch("/api/graph?all=1");
    let data = await res.json();
    if (!(res.ok && !data.error && data.nodes && data.nodes.length)) {
      res = await fetch("/api/graph?q=cpu&min_confidence=0.5");
      data = await res.json();
    }
    if (res.ok && !data.error && data.nodes && data.nodes.length) render(data); else overlay("empty");
  } catch (_) { overlay("empty"); }
}
init();
