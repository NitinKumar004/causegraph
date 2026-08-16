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

from causegraph.graph import attribution, builder, traverse  # noqa: E402
from causegraph.ingest import reader  # noqa: E402


def _peak_rss_mib() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return (ru / (1024 * 1024)) if sys.platform == "darwin" else (ru / 1024)


def gen_rows(rows: int):
    """A fanout-10 process tree of `rows` spawn events (pid i, ppid i//10), each
    followed by one resource.sample so attribution has O(samples) real work."""
    for i in range(1, rows + 1):
        pid = i
        ppid = i // 10  # 0 for the root band
        yield (i * 2, pid, ppid, "process.spawn",
               '{"id":"s%d","ts":%d,"host_id":"b","kind":"process.spawn",'
               '"actor":{"pid":%d,"ppid":%d,"exe":"/bin/x","args":[],"user":"u"},'
               '"source":"poll","confidence":1.0}' % (i, i * 2, pid, ppid))
        yield (i * 2 + 1, pid, ppid, "resource.sample",
               '{"id":"r%d","ts":%d,"host_id":"b","kind":"resource.sample",'
               '"actor":{"pid":%d,"ppid":%d,"exe":"/bin/x","args":[],"user":"u"},'
               '"metrics":{"cpu_pct":%d.5},"source":"poll","confidence":1.0}'
               % (i, i * 2 + 1, pid, ppid, i % 100))


def _percentiles(samples: list[float]) -> dict:
    s = sorted(samples)
    at = lambda p: s[min(len(s) - 1, int(p * (len(s) - 1)))]
    return {"p50": round(at(0.50), 4), "p95": round(at(0.95), 4), "p99": round(at(0.99), 4)}


def measure_query(spawns: int, db_path: str, trials: int = 20) -> dict:
    conn = sqlite3.connect(db_path)
    reader.ensure_schema(conn)
    conn.executemany(
        "INSERT INTO events(ts, pid, ppid, kind, data) VALUES(?,?,?,?,?)",
        gen_rows(spawns),
    )
    conn.commit()
    conn.close()

    events = list(reader.read_events(db_path))  # one read, shared by build + attribution
    t0 = time.perf_counter()
    g = builder.build(events)
    build_s = time.perf_counter() - t0

    # The measured pass: attribution over ALL resource.samples (O(samples)) + rank.
    added = []
    for _ in range(trials):
        t = time.perf_counter()
        attribution.annotate(g, events)
        _ = attribution.rank_by(g, attribution.CPU)
        added.append(time.perf_counter() - t)

    return {
        "spawn_events": spawns,
        "resource_samples": spawns,
        "total_rows": len(events),
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "build_seconds": round(build_s, 3),
        "attribution_added_seconds": _percentiles(added),
        "trials": trials,
        "peak_rss_mib": round(_peak_rss_mib(), 1),
        "note": "build is O(rows) per query (binding limit); attribution adds one O(samples) pass",
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
