"""Resource attribution: instance-window matching, None-safe ranking, pid-reuse."""
from causegraph.graph import attribution, builder
from causegraph.graph.attribution import CPU, RSS
from causegraph.schema import Event


def _spawn(pid, ppid, ts):
    return Event.from_dict({"id": f"s{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": pid, "ppid": ppid, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})


def _exit(pid, ppid, ts):
    return Event.from_dict({"id": f"x{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.exit",
        "actor": {"pid": pid, "ppid": ppid, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})


def _sample(pid, ts, cpu=None, rss=None):
    m = {}
    if cpu is not None:
        m["cpu_pct"] = cpu
    if rss is not None:
        m["rss_bytes"] = rss
    return Event.from_dict({"id": f"r{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "resource.sample",
        "actor": {"pid": pid, "ppid": 1, "exe": "/x", "args": [], "user": "u"},
        "metrics": m, "source": "poll", "confidence": 1.0})


def test_peak_and_rank(why_events):
    g = builder.build(why_events)
    attribution.annotate(g, why_events)
    top_cpu = attribution.rank_by(g, CPU)[0]
    assert g.nodes[top_cpu]["pid"] == 200 and attribution.peak(g, top_cpu, CPU) == 92.5
    top_rss = attribution.rank_by(g, RSS)[0]
    assert g.nodes[top_rss]["pid"] == 300 and attribution.peak(g, top_rss, RSS) == 2147483648


def test_none_sorts_last_no_typeerror(why_events):
    g = builder.build(why_events)
    attribution.annotate(g, why_events)
    ranked = attribution.rank_by(g, RSS)  # only pid 300 has rss; rest None
    assert g.nodes[ranked[0]]["pid"] == 300
    assert all(g.nodes[k]["peak_rss_bytes"] is None for k in ranked[1:])


def test_peak_is_max_over_samples():
    events = [_spawn(5, 1, 10), _sample(5, 20, cpu=30.0), _sample(5, 30, cpu=80.0), _sample(5, 40, cpu=50.0)]
    g = builder.build(events)
    attribution.annotate(g, events)
    key = g.graph["by_pid"][5][0]
    assert attribution.peak(g, key, CPU) == 80.0


def test_sample_attributed_to_correct_instance_on_pid_reuse():
    # pid 5 lives twice; a sample in each window must land on its own instance.
    events = [
        _spawn(5, 1, 10), _sample(5, 15, cpu=40.0), _exit(5, 1, 20),
        _spawn(5, 2, 30), _sample(5, 35, cpu=90.0),
    ]
    g = builder.build(events)
    attribution.annotate(g, events)
    first, second = g.graph["by_pid"][5]  # sorted by spawn_ts: (5,10),(5,30)
    assert attribution.peak(g, first, CPU) == 40.0
    assert attribution.peak(g, second, CPU) == 90.0
