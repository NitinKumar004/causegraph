"""The ppid spawn rule: the one CERTAIN edge (confidence 1.0). Process A spawned
process B because B's parent pid is A and A was live at B's spawn (architecture.md
§5.3). This is the whole of M2's inference; inferred (<1.0) rules arrive in M4.
"""
from __future__ import annotations

from causegraph.graph.edges.base import BuildContext
from causegraph.graph.model import ProcessNode, ProposedEdge


class SpawnRule:
    name = "spawn"

    def propose(self, ctx: BuildContext) -> list[ProposedEdge]:
        edges: list[ProposedEdge] = []
        for child in ctx.nodes:
            if child.ppid == child.pid:
                continue  # guard against a self-parent producing a self-loop
            parent = _parent_live_at(ctx, child)
            if parent is not None:
                edges.append(ProposedEdge(parent.key, child.key, self.name, 1.0))
        return edges


def _parent_live_at(ctx: BuildContext, child: ProcessNode) -> ProcessNode | None:
    """The instance of pid==child.ppid that was live at the child's spawn: the
    latest such instance spawned at or before the child, resolving pid reuse."""
    candidates = ctx.by_pid.get(child.ppid, [])
    best: ProcessNode | None = None
    for p in candidates:
        if p.spawn_ts <= child.spawn_ts and (best is None or p.spawn_ts > best.spawn_ts):
            best = p
    if best is None and not child.observed_spawn and candidates:
        # Inferred (pre-existing) child: its spawn_ts is only "first seen at capture
        # start", so a pre-existing PARENT first sampled a moment later isn't truly
        # "spawned after" it — ts-ordering is meaningless between two baseline
        # processes. Fall back to the ppid instance nearest the child's first-seen
        # time so the real OS process tree still reconstructs (e.g. the recorder
        # daemon, which samples itself at boot before it first enumerates its own
        # parent shell). Observed spawns keep the strict live-at-spawn rule.
        best = min(candidates, key=lambda p: abs(p.spawn_ts - child.spawn_ts))
    return best


