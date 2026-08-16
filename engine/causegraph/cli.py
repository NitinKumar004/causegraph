"""cg — the CauseGraph CLI (architecture.md §5.5). M2 commands:

    cg tree <pid> --db <path>          # the process and its descendants
    cg path <pid> --db <path>          # root-ward ancestry path to <pid>
    cg why "<question>" --db <path>    # explain why a process is hot / using resources
    cg load <jsonl> --db <path>        # dev helper: seed a DB from an events JSONL

The reasoning is pure graph traversal + attribution (traverse.py / attribution.py);
no model is involved. `cg why` uses an offline deterministic narrator by default
(query/narrator.py); a real LLM provider would slot in behind query/llm.py.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Optional, Sequence

import networkx as nx

from causegraph.graph import attribution, builder, traverse
from causegraph.graph.model import NodeKey
from causegraph.ingest import reader
from causegraph.query import llm, resolver
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


def _summary(g: nx.DiGraph, key: NodeKey) -> dict:
    n = g.nodes[key]
    return {"pid": n["pid"], "exe": n["exe"] or "?", "user": n["user"] or "?"}


def cmd_why(args: argparse.Namespace) -> int:
    events = list(reader.read_events(args.db))
    g = builder.build(events)
    attribution.annotate(g, events)  # same event list — one read, no re-scan

    res = resolver.resolve(g, args.question)
    if not res.ok:
        print(res.message, file=sys.stderr)
        return 1

    min_conf = 0.0 if args.all else args.min_confidence
    causes_raw = traverse.causes(g, res.culprit, min_confidence=min_conf)
    if res.value is None and not causes_raw:
        print(f"no attribution or file-cause data for pid {g.nodes[res.culprit]['pid']}",
              file=sys.stderr)
        return 1

    try:
        narrator = llm.get_narrator(args.provider)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    causes = [{"path": g.nodes[c]["path"], "confidence": conf} for c, conf in causes_raw]
    path = [_summary(g, k) for k in traverse.ancestry_path(g, res.culprit)]
    culprit = _summary(g, res.culprit)
    print(narrator.explain(path, culprit, res.metric, res.value, res.assumed, causes=causes))
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

    t = sub.add_parser("tree", help="print a process and its descendants")
    t.add_argument("pid", type=int)
    t.add_argument("--db", default="causegraph.db", help="events database path")
    t.set_defaults(func=cmd_tree)

    pa = sub.add_parser("path", help="print the root-ward ancestry path to a process")
    pa.add_argument("pid", type=int)
    pa.add_argument("--db", default="causegraph.db", help="events database path")
    pa.set_defaults(func=cmd_path)

    wy = sub.add_parser("why", help="explain why a process is hot/using resources")
    wy.add_argument("question", help='e.g. "why is the fan loud?" or "what is using memory"')
    wy.add_argument("--db", default="causegraph.db", help="events database path")
    wy.add_argument("--provider", default=None, help="narrator provider (default: local offline)")
    wy.add_argument("--min-confidence", type=float, default=0.5, dest="min_confidence",
                    help="hide inferred causes below this confidence (default 0.5)")
    wy.add_argument("--all", action="store_true", help="show all causes, even low-confidence")
    wy.set_defaults(func=cmd_why)

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
