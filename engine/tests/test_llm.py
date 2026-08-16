"""llm.py: registry default is the offline narrator; unknown provider errors; Protocol."""
import pytest

from causegraph.query import llm
from causegraph.query.narrator import LocalTemplateNarrator


def test_default_is_local_offline_narrator():
    n = llm.get_narrator()
    assert isinstance(n, LocalTemplateNarrator)
    assert isinstance(n, llm.Narrator)  # satisfies the Protocol
    assert isinstance(llm.get_narrator("local"), LocalTemplateNarrator)


def test_unknown_provider_raises_clearly():
    with pytest.raises(ValueError) as ei:
        llm.get_narrator("gpt-9")
    assert "unknown narrator provider" in str(ei.value)
    assert "gpt-9" in str(ei.value)


def test_registered_provider_is_additive():
    class DummyNarrator:
        def explain(self, path, culprit, metric, value, assumed=False):
            return "dummy"

    llm.register("dummy", DummyNarrator)
    try:
        n = llm.get_narrator("dummy")
        assert isinstance(n, llm.Narrator)
        assert n.explain([], {}, "cpu", 1.0) == "dummy"
    finally:
        llm._REGISTRY.pop("dummy", None)
