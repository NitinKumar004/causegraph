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


def test_exactly_4096_chars_accepted(why_events):
    q = "fan " + "x" * (4096 - 4)  # exactly 4096 chars, contains a cpu keyword
    assert len(q) == 4096
    _, r = _resolved(why_events, q)
    assert r.ok  # boundary is accepted (cap is strictly greater-than)


def test_bare_number_is_not_a_pid(why_events):
    # "more than 50% cpu" must NOT be read as pid 50; it resolves by keyword.
    g, r = _resolved(why_events, "why is it using more than 50% cpu")
    assert r.ok and g.nodes[r.culprit]["pid"] == 200  # hottest cpu, not pid 50
    _, r2 = _resolved(why_events, "top 5 memory hogs")
    assert r2.ok and r2.metric == RSS  # '5' ignored, keyword 'memory' wins


def test_explicit_pid_no_attribution_returns_ok_value_none():
    # AC7b: explicit-pid path returns ok with value=None (does NOT exit here) so
    # cmd_why can still report a file cause; the exit-1-when-nothing decision is cmd_why's.
    from causegraph.schema import Event
    ev = Event.from_dict({"id": "s", "ts": 1, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": 7, "ppid": 1, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})
    g = builder.build([ev])
    attribution.annotate(g, [])  # no samples
    r = resolver.resolve(g, "why is pid 7 slow")
    assert r.ok and r.value is None and g.nodes[r.culprit]["pid"] == 7


def test_empty_db_exit1():
    g = builder.build([])
    attribution.annotate(g, [])
    r = resolver.resolve(g, "why is the fan loud")
    assert not r.ok  # no processes at all


def test_sql_injection_text_is_safe(why_events):
    # A malicious-looking question is only tokenized in memory; no crash, safe fallback.
    g, r = _resolved(why_events, "'; DROP TABLE events; -- cpu")
    assert r.ok and r.metric == CPU  # 'cpu' keyword matched; no SQL executed


def test_no_attribution_data_exit1():
    # graph with a process but zero resource.samples
    g = builder.build([__import__("causegraph.schema", fromlist=["Event"]).Event.from_dict({
        "id": "s", "ts": 1, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": 7, "ppid": 1, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})])
    attribution.annotate(g, [])
    r = resolver.resolve(g, "why is the fan loud")
    assert not r.ok and "attribution" in r.message.lower()
