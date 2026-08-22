"""cg — the CauseGraph CLI (architecture.md §5.5). Commands:

    cg up                              # start the background recorder + serve the live UI
    cg down                            # stop the background recorder
    cg status                          # is it recording? how fresh is the data?
    cg tree <pid> --db <path>          # the process and its descendants
    cg path <pid> --db <path>          # root-ward ancestry path to <pid>
    cg ui   --db <path>                # serve the local read-only causal-graph UI
    cg load <jsonl> --db <path>        # dev helper: seed a DB from an events JSONL

The reasoning is pure graph traversal + attribution (traverse.py / attribution.py);
no model is involved.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Optional, Sequence

import networkx as nx

from causegraph import service
from causegraph.graph import builder, traverse
from causegraph.graph.model import NodeKey
from causegraph.ingest import reader
from causegraph.schema import Event


def _graph_from_db(db_path: str) -> nx.DiGraph:
    return builder.build(reader.read_events(db_path))


def _node_line(g: nx.DiGraph, key: NodeKey) -> str:
    n = g.nodes[key]
    exe = n["exe"] or "?"
    user = n["user"] or "?"
    marker = "" if n["observed_spawn"] else " ~"  # ~ => start inferred (pre-capture)
    return f"{n['pid']} {exe} ({user}){marker}"


def cmd_tree(args: argparse.Namespace) -> int:
    g = _graph_from_db(args.db)
    key = traverse.latest_instance(g, args.pid)
    if key is None:
        print(f"no process with pid {args.pid} in the capture window", file=sys.stderr)
        return 1
    for depth, k in traverse.subtree(g, key):
        print("  " * depth + _node_line(g, k))
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    g = _graph_from_db(args.db)
    key = traverse.latest_instance(g, args.pid)
    if key is None:
        print(f"no process with pid {args.pid} in the capture window", file=sys.stderr)
        return 1
    for depth, k in enumerate(traverse.ancestry_path(g, key)):
        print("  " * depth + _node_line(g, k))
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from causegraph.api import server

    httpd = server.serve(args.db, host=args.host, port=args.port)
    host, port = httpd.server_address
    print(f"CauseGraph UI on http://{host}:{port}  (db={args.db}, Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    """One command to use it: ensure a background recorder is capturing, then serve
    the live UI. Ctrl-C stops only the UI — the recorder keeps recording."""
    import threading

    from causegraph.api import server

    db = args.db or service.default_db()
    try:
        state, pid = service.start(db)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"recorder {state} (pid {pid}) → {db}", flush=True)

    httpd = server.serve(db, host=args.host, port=args.port)
    host, port = httpd.server_address
    url = f"http://{host}:{port}"
    print(f"UI → {url}", flush=True)
    print("Ctrl-C stops the UI; the recorder keeps recording (`cg status` / `cg down`).", flush=True)
    if not args.no_open:
        threading.Timer(0.6, lambda: _try_open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
    print("\nUI stopped. Recorder still running → `cg down` to stop it.")
    return 0


def _try_open(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass  # headless / no browser — the URL is already printed


def cmd_down(args: argparse.Namespace) -> int:
    state, pid = service.stop()
    print(f"recorder stopped (pid {pid})" if state == "stopped" else "no recorder was running")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = args.db or service.default_db()
    s = service.status(db)
    pid = s["recorder_pid"]
    print(f"recorder: {'running (pid %d)' % pid if pid else 'stopped'}")
    print(f"database: {db}")
    if s["events"] is None:
        print("events:   (database not created yet — run `cg up`)")
    else:
        age = s["latest_age_s"]
        fresh = "live" if (age is not None and age < 10) else "frozen/stale"
        agestr = f"{age:.0f}s ago" if age is not None else "—"
        print(f"events:   {s['events']} (newest {agestr}, {fresh})")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    events = []
    with open(args.jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(Event.from_dict(json.loads(line)))
    conn = sqlite3.connect(args.db)
    try:
        reader.write_events(conn, events)
    finally:
        conn.close()
    print(f"loaded {len(events)} events into {args.db}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cg", description="CauseGraph — trace why a process exists.")
    sub = p.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the background recorder and serve the live UI")
    up.add_argument("--db", default=None, help="events database (default: ~/.causegraph/live.db)")
    up.add_argument("--host", default="127.0.0.1", help="bind host (loopback only by default)")
    up.add_argument("--port", type=int, default=8765, help="bind port (0 = ephemeral)")
    up.add_argument("--no-open", action="store_true", help="don't open a browser")
    up.set_defaults(func=cmd_up)

    dn = sub.add_parser("down", help="stop the background recorder")
    dn.set_defaults(func=cmd_down)

    st = sub.add_parser("status", help="show recorder + capture status")
    st.add_argument("--db", default=None, help="events database (default: ~/.causegraph/live.db)")
    st.set_defaults(func=cmd_status)

    t = sub.add_parser("tree", help="print a process and its descendants")
    t.add_argument("pid", type=int)
    t.add_argument("--db", default="causegraph.db", help="events database path")
    t.set_defaults(func=cmd_tree)

    pa = sub.add_parser("path", help="print the root-ward ancestry path to a process")
    pa.add_argument("pid", type=int)
    pa.add_argument("--db", default="causegraph.db", help="events database path")
    pa.set_defaults(func=cmd_path)

    ui = sub.add_parser("ui", help="serve a local read-only web UI for the causal graph")
    ui.add_argument("--db", default="causegraph.db", help="events database path")
    ui.add_argument("--host", default="127.0.0.1", help="bind host (loopback only by default)")
    ui.add_argument("--port", type=int, default=8765, help="bind port (0 = ephemeral)")
    ui.set_defaults(func=cmd_ui)

    lo = sub.add_parser("load", help="seed a database from an events JSONL file (dev helper)")
    lo.add_argument("jsonl")
    lo.add_argument("--db", default="causegraph.db", help="events database path")
    lo.set_defaults(func=cmd_load)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
