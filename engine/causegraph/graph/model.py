"""Graph node/edge types (architecture.md §5.3).

A ProcessNode is one *instance* of a process, identified by (pid, spawn_ts) — not
pid alone — so a reused pid over the retention window is two distinct nodes and a
child never attaches to the wrong parent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# A process node key is (pid, spawn_ts); a file node key is ("file", normpath).
# Both flow through the same graph, so key format + the type guard have ONE owner
# here (ADR 0001).
NodeKey = tuple

FILE_TAG = "file"


def file_key(path: str) -> tuple[str, str]:
    """The sole constructor of a FILE node key — used by both builder and file_watch.
    Normalizes the path here so callers feeding different string forms of the same
    path (raw vs normalized) always produce the identical key (ADR 0001)."""
    return (FILE_TAG, os.path.normpath(path))


def is_process_key(key) -> bool:
    """True for process keys (pid, spawn_ts); False for file keys ("file", path).
    The single guard that attribution routes through so a (str,str) key never enters
    a comparison against an (int,int) key."""
    return isinstance(key[0], int)


def process_keys(g) -> list:
    """All process node keys in g — the single owner of this filter (ADR 0001)."""
    return [k for k in g.nodes() if is_process_key(k)]


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
