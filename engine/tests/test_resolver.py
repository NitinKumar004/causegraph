"""Resolver: keyword→metric, explicit pid, absent pid, empty/too-long, no-attribution."""
from causegraph.graph import attribution, builder
from causegraph.graph.attribution import CPU, RSS
from causegraph.query import resolver


def _resolved(why_events, question):
    g = builder.build(why_events)
    attribution.annotate(g, why_events)
    return g, resolver.resolve(g, question)


def test_cpu_keywords_pick_hottest_cpu(why_events):
    for q in ["why is the fan loud?", "cpu is high", "everything is slow"]:
        g, r = _resolved(why_events, q)
        assert r.ok and r.metric == CPU and not r.assumed
        assert g.nodes[r.culprit]["pid"] == 200 and r.value == 92.5


def test_memory_keywords_pick_hottest_rss(why_events):
    g, r = _resolved(why_events, "what is using memory")
    assert r.ok and r.metric == RSS and not r.assumed
    assert g.nodes[r.culprit]["pid"] == 300 and r.value == 2147483648


def test_no_keyword_falls_back_to_cpu_assumed(why_events):
    g, r = _resolved(why_events, "what is going on here")
    assert r.ok and r.metric == CPU and r.assumed is True
    assert g.nodes[r.culprit]["pid"] == 200


def test_explicit_pid_honored(why_events):
    g, r = _resolved(why_events, "why is pid 100 busy")
    assert r.ok and g.nodes[r.culprit]["pid"] == 100 and r.value == 10.0


def test_explicit_absent_pid_exit1(why_events):
    _, r = _resolved(why_events, "why is pid 99999 slow")
    assert not r.ok and "99999" in r.message


def test_empty_question_exit1(why_events):
    _, r = _resolved(why_events, "   ")
    assert not r.ok and "empty" in r.message.lower()


def test_too_long_question_exit1(why_events):
    _, r = _resolved(why_events, "cpu " * 2000)
    assert not r.ok and "too long" in r.message.lower()


def test_no_attribution_data_exit1():
    # graph with a process but zero resource.samples
    g = builder.build([__import__("causegraph.schema", fromlist=["Event"]).Event.from_dict({
        "id": "s", "ts": 1, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": 7, "ppid": 1, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})])
    attribution.annotate(g, [])
    r = resolver.resolve(g, "why is the fan loud")
    assert not r.ok and "attribution" in r.message.lower()
