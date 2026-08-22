"""Read canonical events out of the SQLite store (architecture.md §5.3 ingest).

The reader only depends on the `data` column (the canonical event JSON) ordered by
`seq`, so it works on any DB the Go daemon wrote and never branches on OS. All SQL
is parameterized; no query is built from user input.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable, Iterator

from causegraph.schema import Event

# Mirrors daemon/internal/store/sqlite.go so Python-created fixture DBs match what
# the daemon writes. The reader itself needs only `seq` and `data`.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    seq  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   INTEGER NOT NULL,
    pid  INTEGER NOT NULL,
    ppid INTEGER NOT NULL,
    kind TEXT    NOT NULL,
    data TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_pid ON events(pid);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (str(1),),
    )
    conn.commit()


def write_events(conn: sqlite3.Connection, events: Iterable[Event]) -> None:
    """Insert events (used to build deterministic fixture DBs)."""
    ensure_schema(conn)
    rows = [
        (e.ts, e.actor.pid, e.actor.ppid, e.kind, json.dumps(e.to_dict(), separators=(",", ":")))
        for e in events
    ]
    conn.executemany(
        "INSERT INTO events(ts, pid, ppid, kind, data) VALUES(?,?,?,?,?)", rows
    )
    conn.commit()


def read_events(db_path: str) -> Iterator[Event]:
    """Yield events from the store in write order (seq)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT data FROM events ORDER BY seq"):
            yield Event.from_dict(json.loads(row["data"]))
    finally:
        conn.close()


def read_events_since(db_path: str, after_seq: int) -> Iterator[tuple[int, Event]]:
    """Yield (seq, Event) for rows with seq > after_seq, in write order — so a caller
    can read only what's new since a prior read instead of re-parsing the whole store."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT seq, data FROM events WHERE seq > ? ORDER BY seq", (after_seq,)):
            yield row["seq"], Event.from_dict(json.loads(row["data"]))
    finally:
        conn.close()


def max_seq(db_path: str) -> int:
    """Highest seq in the store (0 if empty) — a cheap indexed lookup used to detect
    whether anything new was written (and whether the DB was reset)."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(seq) FROM events").fetchone()
        return row[0] or 0
    finally:
        conn.close()


def schema_version(db_path: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()
