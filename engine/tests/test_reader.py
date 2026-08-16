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


def test_reader_only_reads_data_column(tmp_path, graph_events):
    """A DB with only seq+data (plus required NOT NULL cols) still reads."""
    db = str(tmp_path / "min.db")
    conn = sqlite3.connect(db)
    reader.write_events(conn, graph_events)
    conn.close()
    assert len(list(reader.read_events(db))) == len(graph_events)
