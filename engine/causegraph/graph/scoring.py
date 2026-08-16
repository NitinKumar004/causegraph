"""Confidence scoring for inferred edges (architecture.md §5.3): base rule
confidence × provenance × temporal tightness, clamped to [0,1]. Certain edges
(the ppid spawn edge, confidence 1.0) are never rescored.

Per ADR 0001, an edge spanning two events uses the provenance of its WEAKER end
(`min_source`) — an edge is only as trustworthy as its least-trusted signal.
"""
from __future__ import annotations

import math

# Native kernel capture is most trusted; polled process data least. fsnotify file
# events sit between (real-time, but no process attribution).
_PROVENANCE = {
    "native": 1.0, "ebpf": 1.0, "es": 1.0, "etw": 1.0,
    "fsnotify": 0.9,
    "poll": 0.8,
}


def provenance_weight(source: str) -> float:
    return _PROVENANCE.get(source, 0.5)


def min_source(a: str, b: str) -> str:
    """The lower-provenance of two sources (ADR 0001: weaker end wins)."""
    return a if provenance_weight(a) <= provenance_weight(b) else b


def combine(base: float, source: str, dt_ns: int, half_life_ns: int) -> float:
    """base × provenance(source) × exp(-dt/half_life), clamped to [0,1]. Larger dt
    (older signal) → lower; dt<=0 (same instant) → full temporal weight."""
    base = _clamp(base)
    if half_life_ns <= 0:
        temporal = 1.0 if dt_ns <= 0 else 0.0
    else:
        temporal = math.exp(-max(0, dt_ns) / half_life_ns)
    return _clamp(base * provenance_weight(source) * temporal)


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x
