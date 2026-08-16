"""graph_payload contract: nodes/edges/ids/labels, resolution, dedup, bounds, errors."""
import sqlite3

from causegraph.api.server import graph_payload
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


def test_pid_payload_shape_and_file_cause(filewatch_db):
    p = graph_payload(filewatch_db, pid=900)
    ids = {n["id"] for n in p["nodes"]}
    assert p["culprit"] == "p:900:2000"
    assert "p:900:2000" in ids and "f:/etc/app.conf" in ids
    # process node fields
    proc = next(n for n in p["nodes"] if n["id"] == "p:900:2000")
    assert proc["kind"] == "process" and proc["label"] == "/usr/bin/python3 (pid 900)"
    assert proc["observed_spawn"] is True and proc["peak_cpu_pct"] == 40.0
    fnode = next(n for n in p["nodes"] if n["id"] == "f:/etc/app.conf")
    assert fnode["kind"] == "file" and fnode["label"] == "app.conf"
    # the file->process edge carries rule + confidence
    edge = next(e for e in p["edges"] if e["source"] == "f:/etc/app.conf")
    assert edge["target"] == "p:900:2000" and edge["rule"] == "file_watch"
    assert abs(edge["confidence"] - 0.72) < 1e-4
    assert p["truncated"] is False


def test_q_path_resolves(why_db):
    p = graph_payload(why_db, q="why is the fan loud?")
    assert p["culprit"] == "p:200:300"  # hottest cpu process


def test_no_duplicate_culprit(graph_db):
    p = graph_payload(graph_db, pid=100)
    ids = [n["id"] for n in p["nodes"]]
    assert len(ids) == len(set(ids))  # ancestry+subtree both include culprit -> deduped


def test_min_confidence_hides_file_cause(filewatch_db):
    p = graph_payload(filewatch_db, pid=900, min_confidence=0.9)  # 0.72 < 0.9
    assert not any(n["id"] == "f:/etc/app.conf" for n in p["nodes"])


def test_errors_never_raise(tmp_path, graph_db):
    assert graph_payload(graph_db)["error"] == "pid or q required"
    assert "mutually exclusive" in graph_payload(graph_db, pid=1, q="x")["error"]
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
