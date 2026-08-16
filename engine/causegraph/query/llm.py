"""The LLM/narration adapter boundary (architecture.md §5.4). The Narrator
Protocol lives HERE so both llm.py and narrator.py can reference the contract
without an import cycle. `get_narrator` is a pure registry: the default is the
offline templated narrator; a real provider (M5-full) registers here and stays
additive. The narrator only PHRASES — all reasoning already happened in the graph
and traversal. This module never makes a network call.
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class Narrator(Protocol):
    def explain(self, path, culprit, metric, value, assumed: bool = False) -> str:
        """Turn an already-computed causal path + culprit into plain text. Pure
        phrasing: no graph access, no reasoning, no network.

        NOTE: this contract is intentionally thin for the all-1.0-ppid world. When
        M4-full adds inferred edges (per-edge confidence/rule) and M5-full adds a
        real provider, grow it ADDITIVELY via defaulted kwargs (as `assumed=False`
        did) so existing providers keep working — do not treat it as frozen."""
        ...


_REGISTRY: dict[str, Callable[[], Narrator]] = {}


def register(name: str, factory: Callable[[], Narrator]) -> None:
    _REGISTRY[name] = factory


def get_narrator(name: str | None = None) -> Narrator:
    if name is None or name == "local":
        # Lazy import keeps this module free of a top-level narrator dependency.
        from causegraph.query.narrator import LocalTemplateNarrator

        return LocalTemplateNarrator()
    if name in _REGISTRY:
        return _REGISTRY[name]()
    known = ", ".join(["local", *sorted(_REGISTRY)])
    raise ValueError(f"unknown narrator provider: {name!r} (known: {known})")
