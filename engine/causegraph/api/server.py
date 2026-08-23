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
HOT = 90.0  # CPU% at/above which a process is "hot" (matches the UI) — the incident threshold


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


SIBLING_CAP = 8   # immediate siblings shown for context
CHILD_CAP = 12    # children shown per parent — a process with hundreds of kids (cged,
                  # launchd) must stay readable, so show the busiest few, not all
FAMILY_MAX = 60   # overall readable cap for the family view (hundreds of nodes in one
                  # breadthfirst row is unreadable), regardless of max_nodes


def _children_by_cpu(g, key):
    """Spawn children of key, busiest first — so a capped view keeps the ones that matter."""
    return sorted(_spawn_children(g, key), key=lambda k: -(g.nodes[k].get("peak_cpu_pct") or 0))


def _family_payload(g, culprit, min_confidence, max_nodes, expand=False) -> dict:
    """The selection's focused, READABLE causal family: root-ward ancestry spine, file
    causes, a bounded slice of its subtree (top CHILD_CAP children per parent), and a few
    siblings — capped at FAMILY_MAX so a high-fanout node never floods the graph. With
    expand=True the caps widen (user asked to see more)."""
    from collections import deque
    child_cap = 40 if expand else CHILD_CAP
    cap = min(max_nodes, 240 if expand else FAMILY_MAX)
    sel = {}  # id -> key, insertion-ordered
    truncated = False
    def add(k):
        nonlocal truncated
        if len(sel) >= cap:
            truncated = True
            return False
        sel.setdefault(_node_id(k), k)
        return True
    anc = traverse.ancestry_path(g, culprit)  # [root, ..., culprit] — a chain, not a fan
    for k in anc:
        add(k)
    for f, _conf in traverse.causes(g, culprit, min_confidence):  # the causal story first
        add(f)
    dq = deque([culprit])  # bounded BFS of the subtree: busiest CHILD_CAP children per parent
    while dq and len(sel) < cap:
        kids = _children_by_cpu(g, dq.popleft())
        if len(kids) > child_cap:
            truncated = True
        for c in kids[:child_cap]:
            if add(c):
                dq.append(c)
    if len(anc) >= 2:  # immediate siblings only, capped, for context
        sibs = _children_by_cpu(g, anc[-2])
        if len(sibs) > SIBLING_CAP:
            truncated = True
        for c in sibs[:SIBLING_CAP]:
            add(c)
    p = _emit(g, sel.values(), min_confidence, culprit)
    p["truncated"] = truncated
    return p


def proc_detail(db, pid, max_series=90, as_of=None) -> dict:
    """Rich detail for one process instance, for the Inspect view: lifecycle status,
    CPU/RSS now/peak/avg, a resource sample SERIES (for a sparkline), and children it
    spawned. Read-only over the captured events — no live OS query."""
    events, g = _view(db, as_of)
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


# ---- time-travel: view the machine "as of" a past timestamp ----
_ASOF: dict = {"path": None, "as_of": None, "events": None, "g": None}


def _view(db, as_of=None):
    """(events, annotated graph) as of a timestamp — events with ts <= as_of, so the UI
    can rewind. as_of=None is now (the incremental-cached full build). The last as_of build
    is cached so scrubbing at one position doesn't rebuild each request."""
    events, g = _load(db)
    if as_of is None:
        return events, g
    ab = os.path.abspath(db)
    with _CACHE_LOCK:
        if _ASOF["path"] == ab and _ASOF["as_of"] == as_of and _ASOF["events"] is not None:
            return _ASOF["events"], _ASOF["g"]
        ev = [e for e in events if e.ts <= as_of]   # append-only ts, so a past slice is stable
        gg = _rebuild(ev)
        _ASOF.update(path=ab, as_of=as_of, events=ev, g=gg)
        return ev, gg


def window(db) -> dict:
    """[min_ts, max_ts] of the capture — the range for the timeline scrubber."""
    conn = sqlite3.connect(db)
    try:
        lo, hi = conn.execute("SELECT MIN(ts), MAX(ts) FROM events").fetchone()
    except sqlite3.Error:
        lo = hi = None
    finally:
        conn.close()
    return {"min_ts": lo, "max_ts": hi}


def incidents(db, as_of=None, limit=15) -> dict:
    """Auto-detected notable moments as plain-English cards (no model): CPU spikes,
    memory growth (leaks), and crash-loops. One pass over the (cached) events + graph."""
    events, g = _view(db, as_of)
    stat = {}  # pid -> rolling per-process resource stats
    for e in events:
        if e.kind != "resource.sample" or e.metrics is None:
            continue
        s = stat.get(e.actor.pid)
        if s is None:
            s = stat[e.actor.pid] = {"exe": e.actor.exe, "n": 0, "max_cpu": 0.0, "hot": 0,
                                     "first_rss": None, "first_ts": None, "last_rss": None, "last_ts": None}
        s["n"] += 1
        c, r = e.metrics.cpu_pct, e.metrics.rss_bytes
        if c is not None:
            s["max_cpu"] = max(s["max_cpu"], c)
            if c >= HOT:
                s["hot"] += 1
        if r is not None:
            if s["first_ts"] is None or e.ts < s["first_ts"]:
                s["first_rss"], s["first_ts"] = r, e.ts
            if s["last_ts"] is None or e.ts > s["last_ts"]:
                s["last_rss"], s["last_ts"] = r, e.ts
    out = []
    for pid, s in stat.items():
        nm = os.path.basename(s["exe"] or "?") or "?"
        if s["max_cpu"] >= HOT:
            out.append({"kind": "cpu", "severity": s["max_cpu"], "pid": pid, "exe": s["exe"],
                        "title": f"{nm} hit {s['max_cpu']:.0f}% CPU",
                        "detail": f"pid {pid} · sustained high CPU across {s['hot']} samples"})
        fr, lr = s["first_rss"], s["last_rss"]
        if fr and lr and s["n"] >= 8 and lr >= 1.6 * fr and (lr - fr) >= 64 * 1024 * 1024:
            out.append({"kind": "leak", "severity": (lr - fr) / 1e6, "pid": pid, "exe": s["exe"],
                        "title": f"{nm} memory grew",
                        "detail": f"pid {pid} · {_mib(fr)} → {_mib(lr)} over the capture (possible leak)"})
    # crash-loop: many SHORT-LIVED instances of one exe (not long-running worker pools)
    from collections import Counter
    loops = Counter()
    for k in g.nodes():
        if not is_process_key(k):
            continue
        n = g.nodes[k]
        if n.get("observed_spawn") and n.get("exit_ts") and (n["exit_ts"] - n["spawn_ts"]) < 5_000_000_000:
            loops[n["exe"]] += 1
    for exe, cnt in loops.items():
        if cnt >= 5:
            nm = os.path.basename(exe or "?") or "?"
            out.append({"kind": "crashloop", "severity": 1000 + cnt, "pid": None, "exe": exe,
                        "title": f"{nm} respawned {cnt}×",
                        "detail": f"{cnt} short-lived instances · possible crash loop or churn"})
    out.sort(key=lambda i: -i["severity"])
    return {"incidents": out[:limit], "total": len(out)}


def _mib(n):
    return f"{n / (1024 * 1024):.0f} MiB"


def graph_payload(db, pid=None, min_confidence=0.5, max_nodes=DEFAULT_MAX_NODES, show_all=False, family=False, expand=False, as_of=None) -> dict:
    """Return the bounded causal neighborhood of a culprit as {nodes, edges,
    truncated}, or {"error": msg}. With show_all, return the whole process tree.
    Bound (max_nodes) prevents serializing an unbounded graph."""
    max_nodes = max(1, int(max_nodes))  # a payload always has at least the culprit; avoids anc[-0:]
    events, g = _view(db, as_of)

    if show_all:
        return _all_payload(g, max_nodes)

    if pid is None:
        return {"error": "pid required"}
    culprit = traverse.latest_instance(g, pid)
    if culprit is None:
        return {"error": f"no process with pid {pid} in the capture window"}

    if family:
        return _family_payload(g, culprit, min_confidence, max_nodes, expand)

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
                    as_of = int(qs["as_of"][0]) if "as_of" in qs else None
                except (KeyError, ValueError, IndexError):
                    return self._json(400, {"error": "pid required (int); as_of int"})
                try:
                    payload = proc_detail(db, pid, as_of=as_of)
                except Exception:
                    return self._json(500, {"error": "could not read the events database"})
                return self._json(400 if "error" in payload else 200, payload)
            if path == "/api/incidents":
                qs = parse_qs(parsed.query)
                try:
                    as_of = int(qs["as_of"][0]) if "as_of" in qs else None
                except (ValueError, IndexError):
                    return self._json(400, {"error": "as_of must be an integer"})
                try:
                    return self._json(200, incidents(db, as_of=as_of))
                except Exception:
                    return self._json(500, {"error": "could not read the events database"})
            if path == "/api/window":
                return self._json(200, window(db))
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
                if "expand" in qs:
                    kw["expand"] = qs["expand"][0] not in ("0", "false", "no")
                if "pid" in qs:
                    kw["pid"] = int(qs["pid"][0])
                if "min_confidence" in qs:
                    kw["min_confidence"] = float(qs["min_confidence"][0])
                if "max_nodes" in qs:
                    kw["max_nodes"] = int(qs["max_nodes"][0])
                if "as_of" in qs:
                    kw["as_of"] = int(qs["as_of"][0])
            except ValueError:
                return self._json(400, {"error": "pid/max_nodes/as_of must be integers, min_confidence a float"})
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
