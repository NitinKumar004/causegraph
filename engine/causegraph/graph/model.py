"""Graph node/edge types (architecture.md §5.3).

A ProcessNode is one *instance* of a process, identified by (pid, spawn_ts) — not
pid alone — so a reused pid over the retention window is two distinct nodes and a
child never attaches to the wrong parent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# A process node key is (pid, spawn_ts); a file node key is ("file", path).
# Both flow through the same graph, so key format + the type guard have ONE owner
# here (ADR 0001).
NodeKey = tuple

FILE_TAG = "file"


def file_key(path: str) -> tuple[str, str]:
    """The sole constructor of a FILE node key — used by both builder and file_watch."""
    return (FILE_TAG, path)


def is_process_key(key) -> bool:
    """True for process keys (pid, spawn_ts); False for file keys ("file", path).
    The single guard that attribution/traverse route through so a (str,str) key
    never enters a comparison against an (int,int) key."""
    return isinstance(key[0], int)


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
