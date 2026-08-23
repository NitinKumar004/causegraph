"""Reader: streams events out of SQLite in write order; parameterized SQL only."""
import sqlite3

from causegraph.ingest import reader


def test_read_events_order_and_count(graph_db, graph_events):
    got = list(reader.read_events(graph_db))
    assert len(got) == len(graph_events)
    # write_events preserves order; read_events orders by seq.
    assert [e.id for e in got] == [e.id for e in graph_events]


def test_schema_version(graph_db):
    assert reader.schema_version(graph_db) == "1"


def test_incremental_read_since_and_max_seq(graph_db, graph_events):
    # max_seq is the row count here (write order == seq); reading "since" the midpoint
    # returns only the tail, in order — the basis for the API's incremental cache.
    total = reader.max_seq(graph_db)
    assert total == len(graph_events)
    half = total // 2
    tail = list(reader.read_events_since(graph_db, half))
    assert [seq for seq, _ in tail] == list(range(half + 1, total + 1))
    assert list(reader.read_events_since(graph_db, total)) == []  # nothing new


def test_reader_only_reads_data_column(tmp_path, graph_events):
    """A DB with only seq+data (plus required NOT NULL cols) still reads."""
    db = str(tmp_path / "min.db")
    conn = sqlite3.connect(db)
    reader.write_events(conn, graph_events)
    conn.close()
    assert len(list(reader.read_events(db))) == len(graph_events)


def test_backward_compat_reads_m0m2_only_db(tmp_path, graph_events):
    """Rollback claim: a DB containing ONLY M0-M2 kinds/sources (no file.change,
    no fsnotify) reads unchanged with the current reader — additive enum evolution."""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    reader.write_events(conn, graph_events)  # graph_events are all M0-M2 shapes
    conn.close()
    got = list(reader.read_events(db))
    assert [e.kind for e in got] == [e.kind for e in graph_events]
    assert all(e.source == "poll" for e in got)  # no fsnotify in legacy data
