"""Builder: certain (ppid) edges via the registered SpawnRule; pid-reuse yields
two distinct nodes; the EdgeRule seam is honoured (builder runs registered rules)."""
from causegraph.graph import builder
from causegraph.graph.edges.base import BuildContext, EdgeRule
from causegraph.graph.edges.spawn import SpawnRule
from causegraph.graph.model import ProposedEdge
from causegraph.schema import Event


def _spawn(pid, ppid, ts, exe="/x"):
    return Event.from_dict({
        "id": f"s{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": pid, "ppid": ppid, "exe": exe, "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0,
    })


def _exit(pid, ppid, ts):
    return Event.from_dict({
        "id": f"x{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.exit",
        "actor": {"pid": pid, "ppid": ppid, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0,
    })


def _sample(pid, ppid, ts, exe="/x"):
    # a resource.sample with no prior spawn -> an INFERRED (pre-existing) node
    return Event.from_dict({
        "id": f"r{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "resource.sample",
        "actor": {"pid": pid, "ppid": ppid, "exe": exe, "args": [], "user": "u"},
        "metrics": {"cpu_pct": 1.0}, "source": "poll", "confidence": 1.0,
    })


def test_inferred_child_links_to_parent_first_seen_later():
    # Two pre-existing (inferred) processes: the child (pid 20, ppid 10) is first
    # sampled a hair BEFORE its parent (pid 10). ts-ordering is meaningless between
    # baseline processes, so the spawn edge must still form — otherwise the recorder
    # daemon (which samples itself before enumerating its parent shell) shows up as a
    # disconnected lone node and clicking it renders an empty one-box graph.
    events = [_sample(20, 10, 100), _sample(10, 1, 101), _sample(1, 0, 101)]
    g = builder.build(events)
    parent10 = next(k for k in g.nodes if k[0] == 10)
    child20 = next(k for k in g.nodes if k[0] == 20)
    assert g.has_edge(parent10, child20)         # linked despite child seen first
    assert g.edges[parent10, child20]["rule"] == "spawn"


def test_observed_child_keeps_strict_live_at_spawn():
    # An OBSERVED spawn must NOT link to a parent that spawned strictly after it —
    # the relaxed fallback is only for inferred children.
    events = [_spawn(10, 1, 200), _spawn(20, 10, 100)]  # child 20 spawned before parent 10
    g = builder.build(events)
    parent10 = next(k for k in g.nodes if k[0] == 10)
    child20 = next(k for k in g.nodes if k[0] == 20)
    assert not g.has_edge(parent10, child20)


def test_ppid_edges_and_confidence(graph_events):
    g = builder.build(graph_events)
    # nodes: pids 1,100,200,201,300 -> 5 instances
    assert g.number_of_nodes() == 5
    # edges: 100->200, 100->201, 201->300, 1->100  => 4 certain edges
    assert g.number_of_edges() == 4
    for _, _, d in g.edges(data=True):
        assert d["rule"] == "spawn"
        assert d["confidence"] == 1.0


def test_pid_reuse_two_nodes():
    events = [
        _spawn(500, 1, 10, "/first"),
        _exit(500, 1, 20),
        _spawn(500, 2, 30, "/second"),
    ]
    g = builder.build(events)
    keys = g.graph["by_pid"][500]
    assert keys == [(500, 10), (500, 30)], keys
    # The two instances have distinct parents (ppid 1 vs 2) — not collapsed.
    assert g.nodes[(500, 10)]["exe"] == "/first"
    assert g.nodes[(500, 30)]["exe"] == "/second"


def test_parent_resolves_to_instance_live_at_spawn():
    # Parent pid 1 has two instances; child (ppid 1) spawned at 25 must attach to
    # the instance live then (spawn 20), not the later one (spawn 40).
    events = [
        _spawn(1, 0, 5),
        _exit(1, 0, 15),
        _spawn(1, 0, 20),
        _spawn(9, 1, 25),   # child during 2nd instance of pid 1
        _spawn(1, 0, 40),   # a 3rd instance, spawned after the child
    ]
    g = builder.build(events)
    preds = list(g.predecessors((9, 25)))
    assert preds == [(1, 20)], preds


def test_builder_runs_registered_rules_only():
    """No rules => no edges, proving edges come from the rule registry, not the
    builder inlining ppid logic (the EdgeRule seam)."""
    g = builder.build(graph_events_min(), rules=[])
    assert g.number_of_edges() == 0

    g2 = builder.build(graph_events_min(), rules=[SpawnRule()])
    assert g2.number_of_edges() == 1


def graph_events_min():
    return [_spawn(1, 0, 10), _spawn(2, 1, 20)]


def test_custom_rule_is_additive():
    """A brand-new rule adds edges with no builder change (M4 shape)."""
    class TwinRule:
        name = "twin"

        def propose(self, ctx: BuildContext) -> list[ProposedEdge]:
            # deterministic dummy edge between the two known nodes
            keys = sorted(n.key for n in ctx.nodes)
            return [ProposedEdge(keys[0], keys[1], self.name, 0.5)]

    assert isinstance(TwinRule(), EdgeRule)
    g = builder.build(graph_events_min(), rules=[TwinRule()])
    assert g.number_of_edges() == 1
    (_, _, d), = list(g.edges(data=True))
    assert d["rule"] == "twin" and d["confidence"] == 0.5


def test_rule_sees_raw_events_incl_metrics(graph_events):
    """M4-shape: a rule that needs resource.sample metrics gets them from
    ctx.events with NO builder change — the seam the milestone must prove. The
    fixture's pid-100 sample has cpu_pct=55.0."""
    seen_cpu = []

    class ResourcePeekRule:
        name = "resource-peek"

        def propose(self, ctx):
            for e in ctx.events:
                if e.metrics is not None and e.metrics.cpu_pct is not None:
                    seen_cpu.append((e.actor.pid, e.metrics.cpu_pct))
            return []

    builder.build(graph_events, rules=[ResourcePeekRule()])
    assert (100, 55.0) in seen_cpu, f"metrics did not reach the rule: {seen_cpu}"
    assert (1, 0.1) in seen_cpu
