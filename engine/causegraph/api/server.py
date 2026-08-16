"""Local, read-only HTTP API for the web UI (architecture.md §5.5). It serializes
the causal graph the engine already builds — it adds NO reasoning. Bound to
127.0.0.1 only, no mutation routes, and the DB path is fixed at startup (never read
from the query string), so a client can never point the API at an arbitrary file.
"""
from __future__ import annotations

import json
import os
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from causegraph.graph import attribution, builder, traverse
from causegraph.graph.model import is_process_key
from causegraph.ingest import reader
from causegraph.query import resolver

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "ui")

DEFAULT_MAX_NODES = 500


# ---- payload (pure, no HTTP) ----

def _node_id(key) -> str:
    return f"p:{key[0]}:{key[1]}" if is_process_key(key) else f"f:{key[1]}"


def _node_json(g, key) -> dict:
    n = g.nodes[key]
    if is_process_key(key):
        exe = n["exe"] or "?"
        return {
            "id": _node_id(key), "kind": "process",
            "label": f"{exe} (pid {n['pid']})",
            "pid": n["pid"], "exe": n["exe"], "user": n["user"],
            "observed_spawn": n["observed_spawn"],
            "peak_cpu_pct": n["peak_cpu_pct"], "peak_rss_bytes": n["peak_rss_bytes"],
        }
    return {"id": _node_id(key), "kind": "file",
            "label": os.path.basename(n["path"]) or n["path"], "path": n["path"]}


def graph_payload(db, pid=None, q=None, min_confidence=0.5, max_nodes=DEFAULT_MAX_NODES) -> dict:
    """Return the bounded causal neighborhood of a culprit as {nodes, edges,
    truncated}, or {"error": msg}. Bound (max_nodes) prevents a near-root pid or a
    high-fan-out culprit from serializing the whole graph."""
    if (pid is None) == (q is None):
        return {"error": "pid or q required" if pid is None and q is None
                else "pid and q are mutually exclusive"}
    max_nodes = max(1, int(max_nodes))  # a payload always has at least the culprit; avoids anc[-0:]

    events = list(reader.read_events(db))
    g = builder.build(events)
    attribution.annotate(g, events)

    if pid is not None:
        culprit = traverse.latest_instance(g, pid)
        if culprit is None:
            return {"error": f"no process with pid {pid} in the capture window"}
    else:
        res = resolver.resolve(g, q)
        if not res.ok:
            return {"error": res.message}
        culprit = res.culprit

    # Core: culprit + its root-ward ancestry. Normally a short chain, but guard the
    # pathological deep-nesting case so the payload never exceeds max_nodes with
    # truncated=false — keep the ancestors closest to the culprit.
    truncated = False
    anc = traverse.ancestry_path(g, culprit)  # [root, ..., culprit]
    if len(anc) > max_nodes:
        anc = anc[-max_nodes:]
        truncated = True
    selected: dict[str, object] = {_node_id(k): k for k in anc}

    # Fill the remaining budget: file causes (most-confident-first), then
    # descendants (BFS), stopping at max_nodes.
    for fkey, _conf in traverse.causes(g, culprit, min_confidence):
        if len(selected) >= max_nodes:
            truncated = True
            break
        selected.setdefault(_node_id(fkey), fkey)

    dq = deque(sorted(v for v in g.successors(culprit)))
    while dq:
        if len(selected) >= max_nodes:
            truncated = True
            break
        node = dq.popleft()
        nid = _node_id(node)
        if nid in selected:
            continue
        selected[nid] = node
        dq.extend(sorted(g.successors(node)))

    ids = set(selected)
    nodes = [_node_json(g, k) for k in selected.values()]
    edges = [
        {"source": _node_id(u), "target": _node_id(v),
         "rule": d.get("rule"), "confidence": d.get("confidence")}
        for u, v, d in g.edges(data=True)
        if _node_id(u) in ids and _node_id(v) in ids
    ]
    return {"culprit": _node_id(culprit), "nodes": nodes, "edges": edges, "truncated": truncated}


# ---- HTTP (thin wrapper over graph_payload) ----

_CONTENT_TYPE = {".html": "text/html", ".js": "text/javascript"}
_STATIC = {"/": "index.html", "/app.js": "app.js", "/vendor/cytoscape.min.js": "vendor/cytoscape.min.js"}


def make_handler(db: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep tests/quiet
            pass

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path in _STATIC:
                fp = os.path.join(_UI_DIR, _STATIC[path])
                if not os.path.isfile(fp):
                    return self._send(404, b"not found", "text/plain")
                with open(fp, "rb") as f:
                    body = f.read()
                ctype = _CONTENT_TYPE.get(os.path.splitext(fp)[1], "application/octet-stream")
                return self._send(200, body, ctype)
            if path == "/api/graph":
                return self._api(parse_qs(parsed.query))
            return self._send(404, b"not found", "text/plain")

        def _api(self, qs):
            kw = {}
            try:
                if "pid" in qs:
                    kw["pid"] = int(qs["pid"][0])
                if "q" in qs:
                    kw["q"] = qs["q"][0]
                if "min_confidence" in qs:
                    kw["min_confidence"] = float(qs["min_confidence"][0])
            except ValueError:
                return self._json(400, {"error": "pid must be an integer, min_confidence a float"})
            payload = graph_payload(db, **kw)
            status = 400 if "error" in payload else 200
            return self._json(status, payload)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

    return Handler


def serve(db: str, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create (and return, not-yet-serving) a ThreadingHTTPServer bound to host.
    Caller runs serve_forever(); tests can start it in a thread and shut it down.
    host DEFAULTS to loopback (127.0.0.1); a caller passing --host can override it,
    so this is a safe default, not an enforced invariant."""
    return ThreadingHTTPServer((host, port), make_handler(db))
