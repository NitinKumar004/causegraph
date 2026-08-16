"""Deterministic graph traversal — the reasoning, fully under our control, no
model involved (architecture.md §5.4). Backward walk to roots for `cg path`;
descendant subtree for `cg tree`.
"""
from __future__ import annotations

import networkx as nx

from causegraph.graph.model import NodeKey


def latest_instance(g: nx.DiGraph, pid: int) -> NodeKey | None:
    """The most recent instance of pid (highest spawn_ts), or None if unseen."""
    keys = g.graph.get("by_pid", {}).get(pid)
    if not keys:
        return None
    return max(keys, key=lambda k: k[1])  # k = (pid, spawn_ts)


def parent_of(g: nx.DiGraph, key: NodeKey) -> NodeKey | None:
    """The single spawn parent: highest-confidence predecessor along a `spawn`
    edge only. Inferred edges (e.g. file_watch, which point FILE->process) are
    excluded from lineage so a FILE key never enters the ancestry chain (ADR 0001)."""
    preds = [p for p in g.predecessors(key) if g.edges[p, key].get("rule") == "spawn"]
    if not preds:
        return None
    return max(preds, key=lambda p: g.edges[p, key].get("confidence", 0.0))


def causes(g: nx.DiGraph, key: NodeKey, min_confidence: float = 0.5) -> list[tuple[NodeKey, float]]:
    """Incoming inferred (non-spawn) edges — e.g. file_watch file causes — with
    confidence >= min_confidence, most-confident first. Precision-first: weak edges
    are hidden unless min_confidence is lowered."""
    out = []
    for p in g.predecessors(key):
        d = g.edges[p, key]
        if d.get("rule") != "spawn" and d.get("confidence", 0.0) >= min_confidence:
            out.append((p, d.get("confidence", 0.0)))
    out.sort(key=lambda pc: (-pc[1], str(pc[0])))
    return out


def ancestry_path(g: nx.DiGraph, key: NodeKey) -> list[NodeKey]:
    """Root-ward path [root, ..., key]. Cycle-guarded (a spawn DAG is acyclic,
    but pid data is untrusted)."""
    chain: list[NodeKey] = []
    seen: set[NodeKey] = set()
    cur: NodeKey | None = key
    while cur is not None and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = parent_of(g, cur)
    chain.reverse()
    return chain


def descendants_bfs(g: nx.DiGraph, key: NodeKey):
    """Yield descendant process keys in BFS order (immediate children first),
    excluding `key`. Follows spawn successors, deterministic (sorted). A generator
    so a bounded caller can stop early without materializing the whole subtree."""
    from collections import deque

    seen = {key}
    dq = deque(sorted(g.successors(key)))
    while dq:
        n = dq.popleft()
        if n in seen:
            continue
        seen.add(n)
        yield n
        dq.extend(sorted(g.successors(n)))


def subtree(g: nx.DiGraph, key: NodeKey) -> list[tuple[int, NodeKey]]:
    """(depth, key) pairs for key and all descendants, DFS, children sorted by
    (pid, spawn_ts) for deterministic output."""
    out: list[tuple[int, NodeKey]] = []
    seen: set[NodeKey] = set()

    def walk(k: NodeKey, depth: int) -> None:
        if k in seen:
            return
        seen.add(k)
        out.append((depth, k))
        for child in sorted(g.successors(k)):
            walk(child, depth + 1)

    walk(key, 0)
    return out
