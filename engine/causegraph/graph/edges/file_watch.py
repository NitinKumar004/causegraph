"""The file_watch inferred edge (architecture.md §5.3): a file changed shortly
before a process that references it started → the change probably triggered it.
This is the system's FIRST sub-1.0 edge. FILE→process (not writer→reader): with no
process attribution on file events (fsnotify limit) we cannot know who wrote the
file, only that a file the process references changed just before it started.

Precision (ADR 0001) comes from the path-reference match (the changed path must be
the process's exe or an absolute arg), NOT from the confidence threshold.
"""
from __future__ import annotations

import os

from causegraph.graph import scoring
from causegraph.graph.edges.base import BuildContext
from causegraph.graph.model import ProposedEdge, file_key
from causegraph.schema import KIND_FILE_CHANGE, SOURCE_FSNOTIFY, SOURCE_POLL

DEFAULT_WINDOW_NS = 30_000_000_000       # 30s: only changes this recent are eligible
DEFAULT_HALF_LIFE_NS = 60_000_000_000    # 60s > 2s poll interval (spawn_ts is poll-grained)
# Base chosen so a tight, well-matched cause clears the 0.5 default threshold:
# 0.9 x poll-provenance 0.8 = 0.72 ceiling (strong inferred); weak/old causes decay below 0.5.
BASE_CONFIDENCE = 0.9


class FileWatchRule:
    name = "file_watch"

    def __init__(self, window_ns: int = DEFAULT_WINDOW_NS, half_life_ns: int = DEFAULT_HALF_LIFE_NS):
        self.window_ns = window_ns
        self.half_life_ns = half_life_ns

    def propose(self, ctx: BuildContext) -> list[ProposedEdge]:
        # path -> sorted change timestamps, from the raw file.change events.
        changes: dict[str, list[int]] = {}
        for e in ctx.events:
            if e.kind == KIND_FILE_CHANGE and e.target is not None and e.target.path is not None:
                changes.setdefault(os.path.normpath(e.target.path), []).append(e.ts)
        for lst in changes.values():
            lst.sort()
        if not changes:
            return []

        # spawn/file provenance: the weaker end (poll spawn) governs (ADR 0001).
        src = scoring.min_source(SOURCE_FSNOTIFY, SOURCE_POLL)

        edges: list[ProposedEdge] = []
        for node in ctx.nodes:  # process instances only
            for path in _referenced_paths(node):
                ts = _latest_change_within(changes.get(path), node.spawn_ts, self.window_ns)
                if ts is None:
                    continue
                conf = scoring.combine(BASE_CONFIDENCE, src, node.spawn_ts - ts, self.half_life_ns)
                edges.append(ProposedEdge(file_key(path), node.key, self.name, conf))
        return edges


def _referenced_paths(node) -> set[str]:
    """The exe and absolute args (relative args are skipped — no actor.cwd)."""
    paths = set()
    if node.exe:
        paths.add(os.path.normpath(node.exe))
    for a in node.args:
        if os.path.isabs(a):
            paths.add(os.path.normpath(a))
    return paths


def _latest_change_within(tss, spawn_ts: int, window_ns: int):
    """The most recent change at or before spawn_ts and within window_ns of it."""
    if not tss:
        return None
    best = None
    for ts in tss:  # sorted ascending
        if ts <= spawn_ts and (spawn_ts - ts) <= window_ns:
            best = ts
    return best
