"""graph_payload contract: nodes/edges/ids/labels, resolution, dedup, bounds, errors."""
import sqlite3

from causegraph.api.server import graph_payload, proc_detail
from causegraph.ingest import reader
from causegraph.schema import Event


def _db(tmp_path, events, name="api.db"):
    p = str(tmp_path / name)
    conn = sqlite3.connect(p)
    reader.write_events(conn, events)
    conn.close()
    return p


def _spawn(pid, ppid, ts, exe="/x", args=None):
    return Event.from_dict({"id": f"s{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.spawn",
        "actor": {"pid": pid, "ppid": ppid, "exe": exe, "args": args or [], "user": "u"},
        "source": "poll", "confidence": 1.0})


def _fc(path, ts):
    return Event.from_dict({"id": f"fc{ts}-{path}", "ts": ts, "host_id": "h", "kind": "file.change",
        "actor": {"pid": 0, "ppid": 0, "exe": "", "args": [], "user": ""},
        "target": {"path": path}, "source": "fsnotify", "confidence": 1.0})


def _sample(pid, ts, cpu=None, rss=None, exe="/x"):
    m = {}
    if cpu is not None: m["cpu_pct"] = cpu
    if rss is not None: m["rss_bytes"] = rss
    return Event.from_dict({"id": f"r{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "resource.sample",
        "actor": {"pid": pid, "ppid": 1, "exe": exe, "args": [], "user": "u"},
        "metrics": m, "source": "poll", "confidence": 1.0})


def _exit(pid, ppid, ts, exe="/x"):
    return Event.from_dict({"id": f"x{pid}-{ts}", "ts": ts, "host_id": "h", "kind": "process.exit",
        "actor": {"pid": pid, "ppid": ppid, "exe": exe, "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})


def test_incidents_cpu_leak_crashloop(tmp_path):
    from causegraph.api.server import incidents
    events = [_spawn(1, 0, 5)]
    events += [_spawn(200, 1, 10)] + [_sample(200, 20 + i, cpu=95.0) for i in range(3)]       # CPU spike
    events += [_spawn(300, 1, 29)] + [_sample(300, 30 + i, rss=(100 + 20 * i) * 1024 * 1024) for i in range(10)]  # leak
    ts = 200
    for i in range(6):  # crash-loop: 6 short-lived /flap instances
        events += [_spawn(500 + i, 1, ts, exe="/flap"), _exit(500 + i, 1, ts + 1_000_000, exe="/flap")]
        ts += 10
    r = incidents(_db(tmp_path, events, "inc.db"))
    kinds = {i["kind"] for i in r["incidents"]}
    assert {"cpu", "leak", "crashloop"} <= kinds
    loop = next(i for i in r["incidents"] if i["kind"] == "crashloop")
    assert "6" in loop["title"]  # respawned 6×


def test_as_of_excludes_later_events(tmp_path):
    events = [_spawn(1, 0, 5), _spawn(10, 1, 100), _spawn(20, 1, 200)]
    p = graph_payload(_db(tmp_path, events, "asof.db"), show_all=True, as_of=150)
    pids = {n["pid"] for n in p["nodes"]}
    assert 10 in pids and 20 not in pids  # pid 20 spawned at 200 > as_of 150


def test_incremental_cache_trims_ring_buffer(tmp_path):
    """A ring buffer (-max-rows) deletes the oldest rows; the incremental cache must drop
    them too, so a long-departed process no longer shows up and memory stays bounded."""
    import sqlite3
    from causegraph.api import server
    from causegraph.ingest import reader
    p = _db(tmp_path, [_spawn(1, 0, 5), _spawn(100, 1, 10), _spawn(200, 1, 20)], "ring.db")
    server._CACHE.update(path=None, events=None, last_seq=0, min_seq=0, g=None)  # isolate from other tests
    events, _ = server._load(p)
    assert {e.actor.pid for e in events if e.kind == "process.spawn"} == {1, 100, 200}
    # the daemon's ring buffer trims the two oldest rows, then writes a new one
    conn = sqlite3.connect(p)
    conn.execute("DELETE FROM events WHERE seq <= 2"); conn.commit(); conn.close()
    reader.write_events(sqlite3.connect(p), [_spawn(300, 1, 30)])
    events2, _ = server._load(p)
    pids = {e.actor.pid for e in events2 if e.kind == "process.spawn"}
    assert 1 not in pids and 100 not in pids   # trimmed rows dropped from the cache
    assert 200 in pids and 300 in pids         # survivor + new row kept
    server._CACHE.update(path=None, events=None, last_seq=0, min_seq=0, g=None)


def test_concurrent_load_and_read_do_not_crash(tmp_path):
    """ThreadingHTTPServer serves requests concurrently. Readers iterating the events list
    must never see it change size mid-pass while another thread appends new rows."""
    import sqlite3, threading
    from causegraph.api import server
    from causegraph.ingest import reader
    p = _db(tmp_path, [_spawn(1, 0, 5)] + [_spawn(1000 + i, 1, 10 + i) for i in range(200)], "conc.db")
    server._CACHE.update(path=None, events=None, last_seq=0, min_seq=0, g=None)
    server._load(p)
    errors = []
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set() and n < 60:
            reader.write_events(sqlite3.connect(p), [_spawn(5000 + n, 1, 400 + n)])
            server._load(p)  # advances the cache (extends -> rebinds the list)
            n += 1

    def reader_loop():
        try:
            for _ in range(400):
                evs, _ = server._load(p)
                total = sum(1 for e in evs if e.kind == "process.spawn")  # full pass over the returned list
                assert total >= 201
        except Exception as e:  # a "list changed size during iteration" would land here
            errors.append(e)

    ts = [threading.Thread(target=writer)] + [threading.Thread(target=reader_loop) for _ in range(4)]
    for t in ts: t.start()
    for t in ts[1:]: t.join()
    stop.set(); ts[0].join()
    assert not errors, errors
    server._CACHE.update(path=None, events=None, last_seq=0, min_seq=0, g=None)


def test_pid_payload_shape_and_file_cause(filewatch_db):
    p = graph_payload(filewatch_db, pid=900)
    ids = {n["id"] for n in p["nodes"]}
    assert p["culprit"] == "p:900:2000"
    assert "p:900:2000" in ids and "f:/etc/app.conf" in ids
    # process node fields
    proc = next(n for n in p["nodes"] if n["id"] == "p:900:2000")
    assert proc["kind"] == "process" and proc["label"] == "/usr/bin/python3 (pid 900)"
    assert proc["observed_spawn"] is True and proc["peak_cpu_pct"] == 40.0
    assert proc["spawn_ts"] == 2000  # start time for the "started" field
    assert isinstance(proc["args"], list)  # full command line for the detail view
    fnode = next(n for n in p["nodes"] if n["id"] == "f:/etc/app.conf")
    assert fnode["kind"] == "file" and fnode["label"] == "app.conf"
    # the file->process edge carries rule + confidence
    edge = next(e for e in p["edges"] if e["source"] == "f:/etc/app.conf")
    assert edge["target"] == "p:900:2000" and edge["rule"] == "file_watch"
    assert abs(edge["confidence"] - 0.72) < 1e-4
    assert p["truncated"] is False


def test_proc_detail_shape(filewatch_db):
    d = proc_detail(filewatch_db, 900)
    assert d["pid"] == 900 and d["status"] in ("running", "exited")
    assert d["cpu"]["peak"] == 40.0  # matches the attribution peak
    assert isinstance(d["series"], list) and isinstance(d["children"], list)
    assert d["sample_count"] >= 1
    # unknown pid -> clean error, never raises
    assert "error" in proc_detail(filewatch_db, 999999)


def test_show_all_returns_whole_tree(graph_db):
    # graph_db has 5 process instances (pids 1,100,200,201,300) — show_all returns all
    p = graph_payload(graph_db, show_all=True)
    assert "error" not in p
    pids = sorted(n["pid"] for n in p["nodes"])
    assert pids == [1, 100, 200, 201, 300]
    assert p["culprit"] == "p:1:100"  # lowest pid first (root)
    assert p["truncated"] is False


def test_show_all_capped(tmp_path):
    events = [_spawn(1, 0, 5)] + [_spawn(1000 + i, 1, 10 + i) for i in range(30)]
    db = _db(tmp_path, events, "big.db")
    p = graph_payload(db, show_all=True, max_nodes=10)
    assert len(p["nodes"]) == 10 and p["truncated"] is True


def test_family_caps_high_fanout_children(tmp_path):
    # a process with 100 children (e.g. cged spawning workers) must show only a readable
    # slice, not flood the graph — cap the children and flag truncated.
    events = [_spawn(1, 0, 5), _spawn(500, 1, 10)] + [_spawn(1000 + i, 500, 20 + i) for i in range(100)]
    db = _db(tmp_path, events, "wide_children.db")
    p = graph_payload(db, pid=500, family=True)
    kids = sum(1 for n in p["nodes"] if n["kind"] == "process" and n["pid"] >= 1000)
    assert kids <= 12 and p["truncated"] is True
    assert len(p["nodes"]) <= 60  # overall readable cap


def test_family_is_focused_not_flooded_by_high_fanout_ancestor(tmp_path):
    # root pid 1 has 100 direct children; the culprit (pid 5000) is a grandchild via
    # pid 1000. The family view must be the spine + immediate siblings, NOT root's 100
    # children — a high-fanout ancestor (like launchd) can't flood the focused view.
    events = ([_spawn(1, 0, 5)]
              + [_spawn(1000 + i, 1, 10 + i) for i in range(100)]   # 100 children of root
              + [_spawn(5000, 1000, 200)]                            # culprit under pid 1000
              + [_spawn(6000 + i, 1000, 210 + i) for i in range(20)])  # 20 siblings of culprit
    db = _db(tmp_path, events, "fanout_family.db")
    p = graph_payload(db, pid=5000, family=True)
    pids = {n["pid"] for n in p["nodes"] if n["kind"] == "process"}
    assert {1, 1000, 5000} <= pids           # spine present
    assert p["truncated"] is True            # the 20 siblings were capped -> flagged as truncated
    assert len(p["nodes"]) < 40              # focused, not the whole 120-node tree
    others = sum(1 for x in range(1, 100) if (1000 + x) in pids)  # root's OTHER children
    assert others == 0                        # none of launchd-style cousins leaked in
    sibs = sum(1 for x in range(20) if (6000 + x) in pids)        # capped siblings
    assert 0 < sibs <= 8


def test_no_duplicate_culprit(graph_db):
    p = graph_payload(graph_db, pid=100)
    ids = [n["id"] for n in p["nodes"]]
    assert len(ids) == len(set(ids))  # ancestry+subtree both include culprit -> deduped


def test_min_confidence_hides_file_cause(filewatch_db):
    p = graph_payload(filewatch_db, pid=900, min_confidence=0.9)  # 0.72 < 0.9
    assert not any(n["id"] == "f:/etc/app.conf" for n in p["nodes"])


def test_errors_never_raise(tmp_path, graph_db):
    assert graph_payload(graph_db)["error"] == "pid required"
    assert "no process with pid 99999" in graph_payload(graph_db, pid=99999)["error"]
    empty = _db(tmp_path, [], "empty.db")
    assert "error" in graph_payload(empty, pid=1)


def test_max_nodes_caps_subtree(tmp_path):
    # root pid 1 with 50 children -> huge subtree, capped at max_nodes
    events = [_spawn(1, 0, 10)] + [_spawn(1000 + i, 1, 20 + i) for i in range(50)]
    db = _db(tmp_path, events, "wide.db")
    p = graph_payload(db, pid=1, max_nodes=10)
    assert len(p["nodes"]) <= 10 and p["truncated"] is True


def test_max_nodes_caps_file_causes(tmp_path):
    # one process referencing 50 changed files -> 50 file causes, capped
    paths = [f"/etc/f{i}.conf" for i in range(50)]
    events = [_fc(pp, 1000) for pp in paths] + [_spawn(900, 1, 2000, args=paths)]
    db = _db(tmp_path, events, "fanout.db")
    p = graph_payload(db, pid=900, max_nodes=8)
    assert len(p["nodes"]) <= 8 and p["truncated"] is True


def test_deep_ancestry_core_capped(tmp_path):
    # a long spawn chain 1->2->...->30; culprit=30, max_nodes=5 -> core itself
    # exceeds the cap, so it is truncated to <= max_nodes with truncated=True.
    events = [_spawn(i, i - 1, i * 10) for i in range(1, 31)]
    db = _db(tmp_path, events, "deep.db")
    p = graph_payload(db, pid=30, max_nodes=5)
    assert len(p["nodes"]) <= 5 and p["truncated"] is True


def test_max_nodes_zero_is_clamped(tmp_path):
    # max_nodes<=0 is clamped to 1 (culprit only); never returns the whole ancestry.
    events = [_spawn(i, i - 1, i * 10) for i in range(1, 11)]
    db = _db(tmp_path, events, "clamp.db")
    p = graph_payload(db, pid=10, max_nodes=0)
    assert len(p["nodes"]) == 1 and p["truncated"] is True
