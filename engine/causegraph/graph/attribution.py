"""Resource attribution: annotate each process-instance node with the peak CPU/RSS
observed for it, matching resource.sample events to the correct instance by its
[spawn_ts, exit_ts) window (pid-reuse safe). This powers the "which process is
responsible for the spike" ranking the UI surfaces. It is NOT the doc's edges/resource.py
EdgeRule (which would draw a cross-process edge) — that is deferred to M4-full.

Input is the SAME event list the builder consumed (one read, no DB re-scan).
"""
from __future__ import annotations

import networkx as nx

from causegraph.graph.model import NodeKey, process_keys as _process_keys
from causegraph.metrics import CPU, RSS  # re-exported: attribution.CPU/RSS stay valid
from causegraph.schema import Event, KIND_RESOURCE_SAMPLE

_PEAK_ATTR = {CPU: "peak_cpu_pct", RSS: "peak_rss_bytes"}


def annotate(g: nx.DiGraph, events: list[Event]) -> None:
    """Set peak_* (max over the instance's window) AND latest_* (the most recent
    sample — the "current" value a live monitor shows) on every PROCESS node, None if
    never sampled. FILE nodes are skipped (ADR 0001)."""
    for key in _process_keys(g):
        data = g.nodes[key]
        data.setdefault("peak_cpu_pct", None)
        data.setdefault("peak_rss_bytes", None)
        data.setdefault("latest_cpu_pct", None)
        data.setdefault("latest_rss_bytes", None)
        data.setdefault("last_seen_ts", None)  # newest sample ts — "is it still alive?"

    latest_cpu_ts: dict = {}  # key -> ts of the newest cpu sample applied (events may be unsorted)
    latest_rss_ts: dict = {}
    by_pid = g.graph.get("by_pid", {})
    for e in events:
        if e.kind != KIND_RESOURCE_SAMPLE or e.metrics is None:
            continue
        key = _instance_for(g, by_pid, e.actor.pid, e.ts)
        if key is None:
            continue
        node = g.nodes[key]
        node["last_seen_ts"] = e.ts if node["last_seen_ts"] is None else max(node["last_seen_ts"], e.ts)
        if e.metrics.cpu_pct is not None:
            node["peak_cpu_pct"] = _max_opt(node["peak_cpu_pct"], e.metrics.cpu_pct)
            if e.ts >= latest_cpu_ts.get(key, -1):
                node["latest_cpu_pct"] = e.metrics.cpu_pct
                latest_cpu_ts[key] = e.ts
        if e.metrics.rss_bytes is not None:
            node["peak_rss_bytes"] = _max_opt(node["peak_rss_bytes"], e.metrics.rss_bytes)
            if e.ts >= latest_rss_ts.get(key, -1):
                node["latest_rss_bytes"] = e.metrics.rss_bytes
                latest_rss_ts[key] = e.ts


def _max_opt(cur, val):
    return val if cur is None else max(cur, val)


def _instance_for(g: nx.DiGraph, by_pid: dict, pid: int, ts: int) -> NodeKey | None:
    """The instance of pid whose window contains ts: prefer the latest spawn_ts<=ts
    whose exit_ts is None or > ts; fall back to the latest spawn_ts<=ts; else the
    earliest instance (a sample just before an inferred start).

    CAVEAT (offline/polling): when no window contains ts, the fallback can credit a
    spike to an already-exited or earliest instance. Harmless at polling fidelity;
    revisit once M3 native capture tightens spawn/exit timing rather than inheriting
    this silently."""
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
            if exit_ts is None or ts < exit_ts:  # [spawn_ts, exit_ts) — exit exclusive
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
    # Only PROCESS keys — never compare a ("file",path) key against a (pid,ts) key.
    return sorted(_process_keys(g), key=sort_key)


def peak(g: nx.DiGraph, key: NodeKey, metric: str):
    return g.nodes[key][_PEAK_ATTR[metric]]
