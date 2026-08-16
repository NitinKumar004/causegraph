"""The EdgeRule seam — the most important extensibility point (architecture.md
§5.3/§6). Every causal heuristic is a plugin implementing one interface; the
builder runs every registered rule. Adding a new heuristic (M4: file_watch,
socket, cron, resource) is a new file here, never a builder refactor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from causegraph.graph.model import ProcessNode, ProposedEdge
from causegraph.schema import Event


@dataclass
class BuildContext:
    """What a rule may look at. This IS the doc's EventWindow (architecture.md
    §5.3 `propose(window)`): the full stream of raw events for this build — so M4
    rules that need metrics (`resource`), file changes (`file_watch`) or sockets
    (`socket`) get the data they need — PLUS the process instances the builder
    already derived and a pid -> instances index, so the common ppid case avoids
    re-deriving them. Adding an M4 rule is a new file that reads `events`; the
    builder does not change."""

    events: list[Event]
    nodes: list[ProcessNode]
    by_pid: dict[int, list[ProcessNode]]


@runtime_checkable
class EdgeRule(Protocol):
    name: str

    def propose(self, ctx: BuildContext) -> list[ProposedEdge]:
        """Propose causal edges with a confidence in [0, 1]."""
        ...
