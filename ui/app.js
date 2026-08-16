/* CauseGraph UI — fetches the causal graph from the local API and renders it with
   cytoscape.js. No reasoning here; the server already computed the graph. */
"use strict";

const cy = cytoscape({
  container: document.getElementById("cy"),
  style: [
    { selector: "node", style: {
        "label": "data(label)", "font-size": 10, "text-wrap": "wrap", "text-max-width": 160,
        "color": "#ddd", "text-outline-width": 2, "text-outline-color": "#111",
        "border-width": 1, "border-color": "#0006" } },
    { selector: 'node[kind = "process"]', style: {
        "shape": "round-rectangle", "background-color": "#4c78a8",
        "width": "label", "height": 24, "padding": "6px" } },
    { selector: 'node[kind = "file"]', style: {
        "shape": "ellipse", "background-color": "#e0a03a", "width": 22, "height": 22 } },
    { selector: 'node[?inferred]', style: { "border-style": "dashed", "border-width": 2 } },
    { selector: "edge", style: {
        "label": "data(elabel)", "font-size": 8, "color": "#bbb",
        "curve-style": "bezier", "target-arrow-shape": "triangle",
        "width": "data(width)", "line-color": "data(color)", "target-arrow-color": "data(color)",
        "text-rotation": "autorotate" } },
  ],
});

function confColor(c) {            // 1.0 -> green, lower -> amber/red
  if (c == null) return "#888";
  if (c >= 0.99) return "#4c9a4c";
  if (c >= 0.6) return "#8aa84c";
  return "#b06a3a";
}

function render(data) {
  cy.elements().remove();
  const els = [];
  for (const n of data.nodes) {
    els.push({ data: { ...n, inferred: n.kind === "process" && n.observed_spawn === false } });
  }
  for (const e of data.edges) {
    const c = e.confidence;
    const label = c == null ? e.rule : `${e.rule} ${c.toFixed(2)}`;
    els.push({ data: { ...e, id: `${e.source}->${e.target}`, elabel: label,
                       width: c == null ? 1 : 1 + Math.round(c * 3), color: confColor(c) } });
  }
  cy.add(els);
  cy.layout({ name: "breadthfirst", directed: true, padding: 20, spacingFactor: 1.1 }).run();
  if (data.culprit) cy.$id(data.culprit).select();
}

function setStatus(msg) { document.getElementById("status").textContent = msg; }

async function ask() {
  const pid = document.getElementById("pid").value.trim();
  const q = document.getElementById("q").value.trim();
  const params = new URLSearchParams();
  if (pid) params.set("pid", pid);
  else if (q) params.set("q", q);
  else { setStatus("enter a pid or a question"); return; }
  setStatus("loading…");
  try {
    const res = await fetch(`/api/graph?${params}`);
    const data = await res.json();
    if (!res.ok || data.error) { setStatus(data.error || `error ${res.status}`); return; }
    render(data);
    setStatus(`${data.nodes.length} nodes${data.truncated ? " (truncated)" : ""}`);
  } catch (err) {
    setStatus(`request failed: ${err}`);
  }
}

document.getElementById("f").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
