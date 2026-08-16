#!/usr/bin/env python3
"""Scale measurement for the engine query path (Test plan "scale (query)" +
"scale (retention)" rows).

The binding limit the architecture review named: the engine rebuilds the whole
in-memory networkx DAG from up to MaxRows rows on every `cg tree`/`cg path`. This
measures DAG build + traverse latency and peak RSS at a MaxRows-sized DB, and the
effective retention window at 1x and 10x live-pid counts under the row cap.

Usage: PYTHONPATH=engine python3 scripts/scale_query.py [--rows 1000000] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from causegraph.graph import builder, traverse  # noqa: E402
from causegraph.ingest import reader  # noqa: E402


def _peak_rss_mib() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return (ru / (1024 * 1024)) if sys.platform == "darwin" else (ru / 1024)


def gen_spawn_rows(rows: int):
    """A fanout-10 process tree of `rows` spawn events; pid i, ppid i//10."""
    for i in range(1, rows + 1):
        pid = i
        ppid = i // 10  # 0 for the root band
        data = (
            '{"id":"e%d","ts":%d,"host_id":"b","kind":"process.spawn",'
            '"actor":{"pid":%d,"ppid":%d,"exe":"/bin/x","args":[],"user":"u"},'
            '"source":"poll","confidence":1.0}' % (i, i, pid, ppid)
        )
        yield (i, pid, ppid, "process.spawn", data)


def measure_query(rows: int, db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    reader.ensure_schema(conn)
    conn.executemany(
        "INSERT INTO events(ts, pid, ppid, kind, data) VALUES(?,?,?,?,?)",
        gen_spawn_rows(rows),
    )
    conn.commit()
    conn.close()

    t0 = time.perf_counter()
    g = builder.build(reader.read_events(db_path))
    build_s = time.perf_counter() - t0

    # traverse: full subtree from the root band, and a root-ward path from a leaf.
    deep_leaf = traverse.latest_instance(g, rows)
    root = traverse.latest_instance(g, 1)
    t1 = time.perf_counter()
    _ = traverse.subtree(g, root)
    tree_s = time.perf_counter() - t1
    t2 = time.perf_counter()
    _ = traverse.ancestry_path(g, deep_leaf)
    path_s = time.perf_counter() - t2

    return {
        "rows": rows,
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "build_seconds": round(build_s, 3),
        "tree_traverse_seconds": round(tree_s, 4),
        "path_traverse_seconds": round(path_s, 6),
        "peak_rss_mib": round(_peak_rss_mib(), 1),
        "note": "build is O(rows) per query — the binding limit named in the plan",
    }


def measure_retention(max_rows: int, live_pids: int, sample_hz: float) -> dict:
    """Effective window (seconds) retained under a row cap when `live_pids`
    processes each emit one resource.sample per tick at sample_hz."""
    rows_per_second = live_pids * sample_hz
    window_seconds = max_rows / rows_per_second if rows_per_second else float("inf")
    return {
        "live_pids": live_pids,
        "sample_hz": sample_hz,
        "rows_per_second": rows_per_second,
        "max_rows": max_rows,
        "effective_window_seconds": round(window_seconds, 1),
        "effective_window_hours": round(window_seconds / 3600, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "scale.db")
        query = measure_query(args.rows, db)

    max_rows = 1_000_000
    retention = {
        "1x": measure_retention(max_rows, live_pids=300, sample_hz=0.5),
        "10x": measure_retention(max_rows, live_pids=3000, sample_hz=0.5),
        "assumption": "SampleInterval=2s (0.5 Hz); only processes-that-matter sampled, "
        "so live_pids is the sampled subset, not the full process list",
    }

    out = {"scale_query": query, "scale_retention": retention}
    text = json.dumps(out, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
