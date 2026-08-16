"""Cross-language seam (Must not regress) + real kill -9 reliability.

These drive the ACTUAL cged binary so the seam is exercised end to end — a DB the
Go daemon wrote, read by the Python engine — not two hand-mirrored schemas. Skips
cleanly if bin/cged has not been built (``make build`` produces it; ``make test``
builds it first).
"""
import os
import signal
import sqlite3
import subprocess
import time

import pytest

from causegraph.graph import builder
from causegraph.ingest import reader
from causegraph.schema import KIND_HEARTBEAT

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CGED = os.path.join(REPO_ROOT, "bin", "cged")

pytestmark = pytest.mark.skipif(
    not os.path.exists(CGED), reason="bin/cged not built (run `make build`)"
)


def test_daemon_written_db_readable_by_engine(tmp_path):
    """The daemon writes; the engine reads and builds a graph. Real seam."""
    db = str(tmp_path / "seam.db")
    subprocess.run(
        [CGED, "-db", db, "-duration", "1s", "-interval", "100ms", "-heartbeat", "150ms"],
        check=True,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    events = list(reader.read_events(db))
    assert events, "engine read zero events from a daemon-written DB"
    assert any(e.kind == KIND_HEARTBEAT for e in events), "no heartbeat in daemon DB"
    # The engine builds a graph from real capture without error.
    g = builder.build(events)
    assert g.number_of_nodes() > 0
    assert reader.schema_version(db) == "1"


def test_kill9_leaves_rowcount_within_cap(tmp_path):
    """A real SIGKILL mid-capture must leave rows <= MaxRows on reopen — the
    atomic insert+evict transaction is what guarantees it."""
    db = str(tmp_path / "crash.db")
    max_rows = 20
    proc = subprocess.Popen(
        [CGED, "-db", db, "-interval", "40ms", "-heartbeat", "40ms", "-max-rows", str(max_rows)],
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.0)  # let it write several batches past the cap
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

    conn = sqlite3.connect(db)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()
    finally:
        conn.close()
    assert count <= max_rows, f"after kill -9, count={count} exceeds cap {max_rows}"
    # And it is still readable by the engine (not corrupt).
    assert count == len(list(reader.read_events(db)))


def test_store_schema_parity_go_vs_python(tmp_path):
    """The Go store DDL and the Python fixture DDL must agree on the events table
    columns — the store schema has no codegen gate, so this catches cross-language
    drift (e.g. a future additive column added on only one side)."""
    from causegraph.schema import Event

    go_db = str(tmp_path / "go.db")
    subprocess.run([CGED, "-db", go_db, "-duration", "400ms", "-heartbeat", "100ms"],
                   check=True, stderr=subprocess.DEVNULL, timeout=15)

    py_db = str(tmp_path / "py.db")
    conn = sqlite3.connect(py_db)
    reader.write_events(conn, [Event.from_dict({
        "id": "x", "ts": 1, "host_id": "h", "kind": "heartbeat",
        "actor": {"pid": 1, "ppid": 0, "exe": "/x", "args": [], "user": "u"},
        "source": "poll", "confidence": 1.0})])
    conn.close()

    def cols(db):
        c = sqlite3.connect(db)
        try:
            return [(r[1], r[2]) for r in c.execute("PRAGMA table_info(events)")]
        finally:
            c.close()

    assert cols(go_db) == cols(py_db), "events table schema drifted between Go and Python"
