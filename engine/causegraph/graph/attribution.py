"""Resource attribution: annotate each process-instance node with the peak CPU/RSS
observed for it, matching resource.sample events to the correct instance by its
[spawn_ts, exit_ts) window (pid-reuse safe). This powers the resolver's "which
process is responsible for the spike" choice. It is NOT the doc's edges/resource.py
EdgeRule (which would draw a cross-process edge) — that is deferred to M4-full.

Input is the SAME event list the builder consumed (one read, no DB re-scan).
"""
from __future__ import annotations

import networkx as nx

from causegraph.graph.model import NodeKey
from causegraph.schema import Event, KIND_RESOURCE_SAMPLE

CPU = "cpu"
RSS = "rss"
_PEAK_ATTR = {CPU: "peak_cpu_pct", RSS: "peak_rss_bytes"}


def annotate(g: nx.DiGraph, events: list[Event]) -> None:
    """Set peak_cpu_pct / peak_rss_bytes on every node (None if never sampled),
    taking the max over the resource.samples that fall in each instance's window."""
    for _, data in g.nodes(data=True):
        data.setdefault("peak_cpu_pct", None)
        data.setdefault("peak_rss_bytes", None)

    by_pid = g.graph.get("by_pid", {})
    for e in events:
        if e.kind != KIND_RESOURCE_SAMPLE or e.metrics is None:
            continue
        key = _instance_for(g, by_pid, e.actor.pid, e.ts)
        if key is None:
            continue
        node = g.nodes[key]
        if e.metrics.cpu_pct is not None:
            node["peak_cpu_pct"] = _max_opt(node["peak_cpu_pct"], e.metrics.cpu_pct)
        if e.metrics.rss_bytes is not None:
            node["peak_rss_bytes"] = _max_opt(node["peak_rss_bytes"], e.metrics.rss_bytes)


def _max_opt(cur, val):
    return val if cur is None else max(cur, val)


def _instance_for(g: nx.DiGraph, by_pid: dict, pid: int, ts: int) -> NodeKey | None:
    """The instance of pid whose window contains ts: prefer the latest spawn_ts<=ts
    whose exit_ts is None or >= ts; fall back to the latest spawn_ts<=ts; else the
    earliest instance (a sample just before an inferred start)."""
    keys = by_pid.get(pid)
    if not keys:
        return None
    in_window = None
    le = None
    for k in keys:  # sorted by spawn_ts ascending
        spawn_ts = k[1]
        if spawn_ts <= ts:
            le = k
            exit_ts = g.nodes[k]["exit_ts"]
            if exit_ts is None or ts <= exit_ts:
                in_window = k
    return in_window or le or keys[0]


def rank_by(g: nx.DiGraph, metric: str) -> list[NodeKey]:
    """Node keys ordered by peak metric descending; nodes with no sample (None)
    sort last, deterministically (by key), and never raise."""
    attr = _PEAK_ATTR[metric]
    def sort_key(k: NodeKey):
        v = g.nodes[k][attr]
        # None sorts last: (1, 0, key); present sorts first by -value: (0, -v, key)
        return (1, 0.0, k) if v is None else (0, -float(v), k)
    return sorted(g.nodes(), key=sort_key)


def peak(g: nx.DiGraph, key: NodeKey, metric: str):
    return g.nodes[key][_PEAK_ATTR[metric]]
