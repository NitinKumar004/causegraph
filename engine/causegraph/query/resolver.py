"""Resolver: map an English question to a starting node (architecture.md §5.4,
step 1). Deterministic and keyword-based — the reasoning stays in the graph; this
only picks the entry point. A real NLU resolver would slot in behind the same
interface later. No SQL, no shell, no eval: the question is only tokenized in
memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import networkx as nx

from causegraph.graph import attribution, traverse
from causegraph.graph.attribution import CPU, RSS
from causegraph.graph.model import NodeKey

MAX_QUESTION_LEN = 4096

# Sustained CPU is our proxy for heat/fan (no temperature sensor in the generic backend).
_CPU_WORDS = {"fan", "hot", "heat", "loud", "cpu", "slow", "busy", "burning", "spinning", "warm"}
_RSS_WORDS = {"memory", "mem", "ram", "leak", "leaking", "swap", "oom"}
_TOKEN = re.compile(r"[a-z]+")
# Only an explicitly-signalled pid counts — a bare number ("more than 50% cpu")
# must NOT be misread as a pid.
_PID = re.compile(r"\b(?:pid|process|proc)\s*#?\s*(\d+)\b", re.IGNORECASE)


@dataclass
class Resolution:
    ok: bool
    message: str = ""
    culprit: NodeKey | None = None
    metric: str = CPU
    value: float | None = None
    assumed: bool = False


def _metric_for(question: str) -> tuple[str, bool]:
    """(metric, assumed). assumed=True when no keyword matched (default to CPU)."""
    words = set(_TOKEN.findall(question.lower()))
    if words & _RSS_WORDS:
        return RSS, False
    if words & _CPU_WORDS:
        return CPU, False
    return CPU, True


def resolve(g: nx.DiGraph, question: str) -> Resolution:
    q = (question or "").strip()
    if not q:
        return Resolution(False, "empty question — ask e.g. \"why is the fan loud?\"")
    if len(q) > MAX_QUESTION_LEN:
        return Resolution(False, f"question too long (>{MAX_QUESTION_LEN} chars)")

    metric, assumed = _metric_for(q)

    # Explicit pid in the question is honored (bypasses ranking).
    m = _PID.search(q)
    if m:
        pid = int(m.group(1))
        key = traverse.latest_instance(g, pid)
        if key is None:
            return Resolution(False, f"no process with pid {pid} in the capture window")
        value = attribution.peak(g, key, metric)
        if value is None:
            return Resolution(False, f"no {metric.upper()} attribution data for pid {pid}")
        return Resolution(True, culprit=key, metric=metric, value=value, assumed=assumed)

    # Otherwise pick the hottest node by the chosen metric.
    ranked = attribution.rank_by(g, metric)
    if not ranked:
        return Resolution(False, "no processes in the capture window")
    top = ranked[0]
    value = attribution.peak(g, top, metric)
    if value is None:
        return Resolution(False, "no resource-attribution data in the capture window")
    return Resolution(True, culprit=top, metric=metric, value=value, assumed=assumed)
