"""Local, read-only HTTP API for the web UI (architecture.md §5.5). It serializes
the causal graph the engine already builds — it adds NO reasoning. Bound to
127.0.0.1 only, no mutation routes, and the DB path is fixed at startup (never read
from the query string), so a client can never point the API at an arbitrary file.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from causegraph.graph import attribution, builder, traverse
from causegraph.graph.model import is_process_key
from causegraph.ingest import reader

# Reading+parsing the whole event log dominates query cost (~4.3s to parse ~1M events;
# build+annotate is only ~0.8s). So we keep the parsed events in memory and read only NEW
# rows (seq > last) each request, then rebuild+annotate from memory. First request pays the
# full parse; every one after reads a tiny delta — fast even while the recorder writes.
_CACHE: dict = {"path": None, "events": None, "last_seq": 0, "g": None}
_CACHE_LOCK = threading.Lock()


def _rebuild(events):
    g = builder.build(events)
    attribution.annotate(g, events)
    return g


def _load(db):
    """(events, annotated graph) for db. Incremental: reuse the in-memory events and append
    only rows written since the last read; full reload if the DB was reset/replaced."""
    ab = os.path.abspath(db)
    with _CACHE_LOCK:
        top = reader.max_seq(db)  # cheap indexed MAX(seq); also detects reset (seq restarts)
        fresh = _CACHE["path"] != ab or top < _CACHE["last_seq"]
        if fresh:
            events = list(reader.read_events(db))
            g = _rebuild(events)
            _CACHE.update(path=ab, events=events, last_seq=top, g=g)
            return events, g
        if top == _CACHE["last_seq"]:
            return _CACHE["events"], _CACHE["g"]  # nothing new — reuse the built graph
        delta = list(reader.read_events_since(db, _CACHE["last_seq"]))
        _CACHE["events"].extend(e for _, e in delta)
        _CACHE["last_seq"] = top
        _CACHE["g"] = _rebuild(_CACHE["events"])
        return _CACHE["events"], _CACHE["g"]


def _latest_ts(db: str):
    """The newest event timestamp in the store, or None if unreadable/empty. The UI
    polls this to tell a live capture (ts advancing) from a frozen one (a recorder
    that has stopped writing) — a cheap MAX(ts), no full graph rebuild."""
    try:
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT MAX(ts) FROM events").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None

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
            "args": list(n.get("args") or []),  # full command line, for the detail view
            "observed_spawn": n["observed_spawn"], "spawn_ts": n["spawn_ts"],
            "peak_cpu_pct": n["peak_cpu_pct"], "peak_rss_bytes": n["peak_rss_bytes"],
            "cpu_pct": n.get("latest_cpu_pct"), "rss_bytes": n.get("latest_rss_bytes"),  # "current"
            "last_seen_ts": n.get("last_seen_ts"),  # for the UI's alive/exited check
        }
    return {"id": _node_id(key), "kind": "file",
            "label": os.path.basename(n["path"]) or n["path"], "path": n["path"]}


def _all_payload(g, max_nodes: int) -> dict:
    """The whole captured process tree: all process nodes (sorted by pid so the
    root/launchd comes first), capped at max_nodes, with the edges among them."""
    procs = sorted((k for k in g.nodes() if is_process_key(k)),
                   key=lambda k: (g.nodes[k]["pid"], k[1]))
    truncated = len(procs) > max_nodes
    sel = procs[:max_nodes]
    ids = {_node_id(k) for k in sel}
    nodes = [_node_json(g, k) for k in sel]
    edges = [{"source": _node_id(u), "target": _node_id(v), "rule": d.get("rule"), "confidence": d.get("confidence")}
             for u, v, d in g.edges(data=True) if _node_id(u) in ids and _node_id(v) in ids]
    return {"culprit": _node_id(sel[0]) if sel else None, "nodes": nodes, "edges": edges, "truncated": truncated}


def _spawn_children(g, key):
    return [v for v in g.successors(key) if g.edges[key, v].get("rule") == "spawn"]


def _emit(g, keys, min_confidence, culprit):
    ids = {_node_id(k) for k in keys}
    nodes = [_node_json(g, k) for k in keys]
    edges = [{"source": _node_id(u), "target": _node_id(v), "rule": d.get("rule"), "confidence": d.get("confidence")}
             for u, v, d in g.edges(data=True) if _node_id(u) in ids and _node_id(v) in ids
             and (d.get("rule") != "file_watch" or (d.get("confidence") or 0) >= min_confidence)]
    return {"culprit": _node_id(culprit), "nodes": nodes, "edges": edges}


SIBLING_CAP = 8  # immediate siblings shown for context; a root child (e.g. launchd
                 # with hundreds of kids) must not flood the focused family view.


def _family_payload(g, culprit, min_confidence, max_nodes) -> dict:
    """The selection's focused causal family: its root-ward ancestry spine, its file
    causes, its own descendants, and a bounded set of immediate siblings — capped at
    max_nodes. We add causes/subtree BEFORE siblings so a high-fanout parent (launchd
    has 500+ children) can never crowd the actual causal story out of the budget."""
    sel = {}  # id -> key, insertion-ordered
    def add(k):
        if len(sel) < max_nodes:
            sel.setdefault(_node_id(k), k)
    anc = traverse.ancestry_path(g, culprit)  # [root, ..., culprit] — a chain, not a fan
    for k in anc:
        add(k)
    for f, _conf in traverse.causes(g, culprit, min_confidence):  # the causal story first
        add(f)
    for d in traverse.descendants_bfs(g, culprit):  # selection's own subtree
        add(d)
    if len(anc) >= 2:  # immediate siblings only, capped, for context
        for c in sorted(_spawn_children(g, anc[-2]))[:SIBLING_CAP]:
            add(c)
    truncated = len(sel) >= max_nodes
    p = _emit(g, sel.values(), min_confidence, culprit)
    p["truncated"] = truncated
    return p


def proc_detail(db, pid, max_series=90) -> dict:
    """Rich detail for one process instance, for the Inspect view: lifecycle status,
    CPU/RSS now/peak/avg, a resource sample SERIES (for a sparkline), and children it
    spawned. Read-only over the captured events — no live OS query."""
    events, g = _load(db)
    key = traverse.latest_instance(g, pid)
    if key is None:
        return {"error": f"no process with pid {pid} in the capture window"}
    n = g.nodes[key]
    spawn_ts, exit_ts = n["spawn_ts"], n["exit_ts"]
    samples = []  # (ts, cpu, rss) for THIS instance's live window
    for e in events:
        if (e.kind == "resource.sample" and e.actor.pid == pid and e.metrics is not None
                and e.ts >= spawn_ts and (exit_ts is None or e.ts <= exit_ts)):
            samples.append((e.ts, e.metrics.cpu_pct, e.metrics.rss_bytes))
    samples.sort(key=lambda s: s[0])
    cpus = [c for _, c, _ in samples if c is not None]
    rsses = [r for _, _, r in samples if r is not None]
    return {
        "pid": pid, "exe": n["exe"], "user": n["user"], "args": list(n["args"] or []),
        "status": "exited" if exit_ts else "running",
        "spawn_ts": spawn_ts, "exit_ts": exit_ts,
        "cpu": {"now": cpus[-1] if cpus else None, "peak": max(cpus) if cpus else None,
                "avg": (sum(cpus) / len(cpus)) if cpus else None},
        "rss": {"now": rsses[-1] if rsses else None, "peak": max(rsses) if rsses else None},
        "series": [{"ts": ts, "cpu": c, "rss": r} for ts, c, r in samples[-max_series:]],
        "sample_count": len(samples),
        "children": [{"pid": g.nodes[v]["pid"], "exe": g.nodes[v]["exe"]} for v in _spawn_children(g, key)],
    }


def graph_payload(db, pid=None, min_confidence=0.5, max_nodes=DEFAULT_MAX_NODES, show_all=False, family=False) -> dict:
    """Return the bounded causal neighborhood of a culprit as {nodes, edges,
    truncated}, or {"error": msg}. With show_all, return the whole process tree.
    Bound (max_nodes) prevents serializing an unbounded graph."""
    max_nodes = max(1, int(max_nodes))  # a payload always has at least the culprit; avoids anc[-0:]
    events, g = _load(db)

    if show_all:
        return _all_payload(g, max_nodes)

    if pid is None:
        return {"error": "pid required"}
    culprit = traverse.latest_instance(g, pid)
    if culprit is None:
        return {"error": f"no process with pid {pid} in the capture window"}

    if family:
        return _family_payload(g, culprit, min_confidence, max_nodes)

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

    for node in traverse.descendants_bfs(g, culprit):  # BFS walk owned by traverse.py
        if len(selected) >= max_nodes:
            truncated = True
            break
        selected.setdefault(_node_id(node), node)

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
            if path == "/api/proc":
                qs = parse_qs(parsed.query)
                try:
                    pid = int(qs["pid"][0])
                except (KeyError, ValueError, IndexError):
                    return self._json(400, {"error": "pid required"})
                try:
                    payload = proc_detail(db, pid)
                except Exception:
                    return self._json(500, {"error": "could not read the events database"})
                return self._json(400 if "error" in payload else 200, payload)
            if path == "/api/meta":
                return self._json(200, {"db": os.path.basename(db), "latest_ts": _latest_ts(db)})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # The only mutation route. Guarded two ways: the server binds 127.0.0.1
            # only, and a custom header is required — a cross-origin page in the
            # user's browser cannot set it (it triggers a CORS preflight this server
            # never approves), which blocks drive-by CSRF against localhost.
            if self.headers.get("X-CauseGraph") != "1":
                return self._json(403, {"error": "forbidden"})
            parsed = urlparse(self.path)
            if parsed.path != "/api/kill":
                return self._json(404, {"error": "not found"})
            try:
                pid = int(parse_qs(parsed.query)["pid"][0])
            except (KeyError, ValueError, IndexError):
                return self._json(400, {"error": "pid required"})
            try:
                os.kill(pid, signal.SIGKILL)
                return self._json(200, {"ok": True, "pid": pid})
            except ProcessLookupError:
                return self._json(404, {"error": f"no process {pid}"})
            except PermissionError:
                return self._json(403, {"error": f"not permitted to kill {pid}"})
            except OSError as e:
                return self._json(500, {"error": str(e)})

        def _api(self, qs):
            kw = {}
            try:
                if "all" in qs:
                    kw["show_all"] = qs["all"][0] not in ("0", "false", "no")
                if "family" in qs:
                    kw["family"] = qs["family"][0] not in ("0", "false", "no")
                if "pid" in qs:
                    kw["pid"] = int(qs["pid"][0])
                if "min_confidence" in qs:
                    kw["min_confidence"] = float(qs["min_confidence"][0])
                if "max_nodes" in qs:
                    kw["max_nodes"] = int(qs["max_nodes"][0])
            except ValueError:
                return self._json(400, {"error": "pid/max_nodes must be integers, min_confidence a float"})
            try:
                payload = graph_payload(db, **kw)
            except Exception:
                # operational failure (e.g. an unreadable/corrupt db path fixed at
                # startup) — return a clean JSON error, never a stack-trace 500.
                return self._json(500, {"error": "could not read the events database"})
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
