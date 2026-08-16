"""Deterministic, offline narrator (architecture.md §5.4 — the narrator collapses
a causal path into plain English). It PHRASES only; it does no graph lookups and
no reasoning. Implements the Narrator Protocol (defined in llm.py) structurally,
so there is no import back to llm.py.

`path` / `culprit` are plain dicts {pid, exe, user} the caller resolved from the
graph, keeping all graph access out of here.
"""
from __future__ import annotations

from causegraph.metrics import CPU  # neutral constant; narrator never touches the graph

_METRIC_LABEL = {CPU: "CPU", "rss": "memory"}


def _humanize_bytes(n: float) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TiB"


def _value_str(metric: str, value) -> str:
    return f"{float(value):.1f}%" if metric == CPU else _humanize_bytes(value)


class LocalTemplateNarrator:
    """The default provider: templated English, identical output for identical input."""

    def explain(self, path, culprit, metric, value, assumed: bool = False, causes=None) -> str:
        label = _METRIC_LABEL[metric]
        if value is not None:
            lines = [
                f"{culprit['exe']} (pid {culprit['pid']}) is the likely cause: "
                f"peak {label} {_value_str(metric, value)}."
            ]
        else:
            # No resource data — this is a file-caused answer; lead with the process.
            lines = [f"{culprit['exe']} (pid {culprit['pid']}):"]
        if assumed:
            lines.append(
                "(No metric keyword recognized in the question — assuming a CPU/heat issue.)"
            )
        if metric == CPU and value is not None:
            lines.append(
                "Note: heat/fan is inferred from sustained CPU; no temperature sensor is available."
            )
        for cause in causes or []:
            lines.append(
                f"Possibly triggered by a recent change to {cause['path']} "
                f"(confidence {cause['confidence']:.2f})."
            )
        # ancestry: path is [root, ..., culprit]; show parents most-recent-first.
        ancestors = list(reversed(path[:-1])) if len(path) > 1 else []
        if ancestors:
            lines.append("Started by (most recent first):")
            for node in ancestors:
                lines.append(f"  {node['exe']} (pid {node['pid']})")
        else:
            lines.append("No captured parent (root of the capture window).")
        return "\n".join(lines)
