"""The EdgeRule seam — the most important extensibility point (architecture.md
§5.3/§6). Every causal heuristic is a plugin implementing one interface; the
builder runs every registered rule. Adding a new heuristic (M4: file_watch,
socket, cron, resource) is a new file here, never a builder refactor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from causegraph.graph.model import ProcessNode, ProposedEdge


@dataclass
class BuildContext:
    """What a rule may look at: the process instances the builder derived, plus a
    pid -> instances index (sorted by spawn_ts) so rules avoid re-scanning."""

    nodes: list[ProcessNode]
    by_pid: dict[int, list[ProcessNode]]


@runtime_checkable
class EdgeRule(Protocol):
    name: str

    def propose(self, ctx: BuildContext) -> list[ProposedEdge]:
        """Propose causal edges with a confidence in [0, 1]."""
        ...
