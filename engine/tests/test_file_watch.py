"""file_watch rule + builder file nodes + traverse causes/parent_of guard."""
from causegraph.graph import builder, traverse
from causegraph.graph.edges.file_watch import FileWatchRule
from causegraph.graph.model import file_key, is_process_key
from causegraph.schema import Event


def _fc(path, ts):
    return Event.from_dict({"id": f"fc{ts}", "ts": ts, "host_id": "h", "kind": "file.change",
        "actor": {"pid": 0, "ppid": 0, "exe": "", "args": [], "user": ""},
        "target": {"path": path}, "source": "fsnotify", "confidence": 1.0})


def _spawn(pid, ppid, ts, exe="/x", args=None):
    return Event.from_dict({"id": f"s{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": pid, "ppid": ppid, "exe": exe, "args": args or [], "user": "u"},
        "source": "poll", "confidence": 1.0})


def test_builder_creates_file_node_not_process(filewatch_events):
    g = builder.build(filewatch_events)
    assert g.has_node(file_key("/etc/app.conf"))
    assert g.nodes[file_key("/etc/app.conf")]["kind"] == "file"
    # pid-0 sentinel from file.change must NOT create a process node
    assert 0 not in g.graph["by_pid"]
    assert all(is_process_key(k) for k in g.graph["by_pid"][900])


def test_file_watch_edge_present_with_confidence(filewatch_events):
    g = builder.build(filewatch_events)
    proc = g.graph["by_pid"][900][0]
    fnode = file_key("/etc/app.conf")
    assert g.has_edge(fnode, proc)
    d = g.edges[fnode, proc]
    assert d["rule"] == "file_watch"
    assert abs(d["confidence"] - 0.72) < 1e-4  # 0.9 * poll 0.8 * ~1.0


def test_no_edge_when_path_not_referenced():
    # process does not reference the changed file -> no edge
    events = [_fc("/etc/other.conf", 1000), _spawn(900, 1, 2000, args=["/etc/app.conf"])]
    g = builder.build(events)
    assert g.number_of_edges() == 0


def test_no_edge_when_change_after_spawn():
    events = [_spawn(900, 1, 1000, args=["/etc/app.conf"]), _fc("/etc/app.conf", 2000)]
    g = builder.build(events)
    assert not g.has_edge(file_key("/etc/app.conf"), (900, 1000))


def test_no_edge_beyond_window():
    # change 40s before spawn (> 30s window) -> ineligible
    events = [_fc("/etc/app.conf", 0), _spawn(900, 1, 40_000_000_000, args=["/etc/app.conf"])]
    g = builder.build(events)
    assert g.number_of_edges() == 0


def test_relative_arg_not_matched():
    # relative arg is skipped (no actor.cwd) -> no false match
    events = [_fc("/etc/app.conf", 1000), _spawn(900, 1, 2000, args=["app.conf"])]
    g = builder.build(events)
    assert g.number_of_edges() == 0


def test_parent_of_ignores_file_edge_and_causes_returns_it(filewatch_events):
    g = builder.build(filewatch_events)
    proc = g.graph["by_pid"][900][0]
    # process 900 has ppid 1 (not captured) -> no spawn parent; the file edge must
    # NOT be mistaken for lineage.
    assert traverse.parent_of(g, proc) is None
    assert traverse.ancestry_path(g, proc) == [proc]  # no file key in lineage
    causes = traverse.causes(g, proc, min_confidence=0.5)
    assert len(causes) == 1 and causes[0][0] == file_key("/etc/app.conf")


def test_causes_min_confidence_filter(filewatch_events):
    g = builder.build(filewatch_events)
    proc = g.graph["by_pid"][900][0]
    assert traverse.causes(g, proc, min_confidence=0.9) == []   # 0.72 < 0.9, hidden
    assert len(traverse.causes(g, proc, min_confidence=0.0)) == 1
