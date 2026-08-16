"""Graph node/edge types (architecture.md §5.3).

A ProcessNode is one *instance* of a process, identified by (pid, spawn_ts) — not
pid alone — so a reused pid over the retention window is two distinct nodes and a
child never attaches to the wrong parent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# A node key is (pid, spawn_ts).
NodeKey = tuple[int, int]


@dataclass
class ProcessNode:
    pid: int
    ppid: int
    spawn_ts: int
    exit_ts: Optional[int] = None
    exe: str = ""
    args: list[str] = field(default_factory=list)
    user: str = ""
    observed_spawn: bool = False  # False => start time inferred (process pre-dated capture)

    @property
    def key(self) -> NodeKey:
        return (self.pid, self.spawn_ts)


@dataclass(frozen=True)
class ProposedEdge:
    parent: NodeKey
    child: NodeKey
    rule: str
    confidence: float
