"""Assemble the in-memory causal DAG from a flat event stream (architecture.md
§5.3). Events are segmented into process *instances* (resolving pid reuse), then
every registered EdgeRule proposes edges. networkx, in-memory, built at query
time — no graph database (the working set is one machine, a few hours).
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

import networkx as nx

from causegraph.graph.edges import DEFAULT_RULES
from causegraph.graph.edges.base import BuildContext, EdgeRule
from causegraph.graph.model import ProcessNode, file_key
from causegraph.schema import (
    Event,
    KIND_FILE_CHANGE,
    KIND_PROCESS_EXIT,
    KIND_PROCESS_SPAWN,
)


def _segment(events: Sequence[Event]) -> list[ProcessNode]:
    """Turn per-pid event sequences into process instances. A spawn starts a new
    instance; an exit closes the current one; any other event (resource.sample,
    heartbeat) attaches to the current instance or starts an inferred one for a
    process that pre-dated capture. file.change events are NOT process events (they
    carry a pid-0 sentinel actor) and are excluded here — they become FILE nodes."""
    by_pid: dict[int, list[Event]] = {}
    for e in events:
        if e.kind == KIND_FILE_CHANGE:
            continue
        by_pid.setdefault(e.actor.pid, []).append(e)

    nodes: list[ProcessNode] = []
    for pid, evs in by_pid.items():
        current: Optional[ProcessNode] = None
        for e in evs:
            a = e.actor
            if e.kind == KIND_PROCESS_SPAWN:
                current = ProcessNode(
                    pid=pid, ppid=a.ppid, spawn_ts=e.ts, exe=a.exe,
                    args=list(a.args), user=a.user, observed_spawn=True,
                )
                nodes.append(current)
            elif e.kind == KIND_PROCESS_EXIT:
                if current is None:
                    current = ProcessNode(
                        pid=pid, ppid=a.ppid, spawn_ts=e.ts, exit_ts=e.ts,
                        exe=a.exe, args=list(a.args), user=a.user, observed_spawn=False,
                    )
                    nodes.append(current)
                else:
                    current.exit_ts = e.ts
                current = None  # instance closed
            else:
                if current is None:
                    current = ProcessNode(
                        pid=pid, ppid=a.ppid, spawn_ts=e.ts, exe=a.exe,
                        args=list(a.args), user=a.user, observed_spawn=False,
                    )
                    nodes.append(current)
                elif not current.exe and a.exe:
                    current.exe = a.exe  # backfill attrs a bare spawn may have lacked
    return nodes


def build(events: Iterable[Event], rules: Optional[list[EdgeRule]] = None) -> nx.DiGraph:
    rules = DEFAULT_RULES if rules is None else rules
    evs = sorted(events, key=lambda e: e.ts)  # stable within equal ts
    nodes = _segment(evs)

    by_pid: dict[int, list[ProcessNode]] = {}
    for n in nodes:
        by_pid.setdefault(n.pid, []).append(n)
    for lst in by_pid.values():
        lst.sort(key=lambda n: n.spawn_ts)

    g = nx.DiGraph()
    for n in nodes:
        g.add_node(
            n.key, kind="process", pid=n.pid, ppid=n.ppid, spawn_ts=n.spawn_ts,
            exit_ts=n.exit_ts, exe=n.exe, user=n.user, args=n.args,
            observed_spawn=n.observed_spawn,
        )

    # FILE nodes: one per distinct path (builder owns kind->node — ADR 0001).
    file_changes: dict[str, list[int]] = {}
    for e in evs:
        if e.kind == KIND_FILE_CHANGE and e.target is not None and e.target.path is not None:
            file_changes.setdefault(e.target.path, []).append(e.ts)
    for path, tss in file_changes.items():
        g.add_node(file_key(path), kind="file", path=path, changes=sorted(tss))

    ctx = BuildContext(events=evs, nodes=nodes, by_pid=by_pid)
    for rule in rules:
        for pe in rule.propose(ctx):
            if g.has_node(pe.parent) and g.has_node(pe.child):
                g.add_edge(pe.parent, pe.child, rule=pe.rule, confidence=pe.confidence)

    g.graph["by_pid"] = {pid: [n.key for n in lst] for pid, lst in by_pid.items()}
    return g
