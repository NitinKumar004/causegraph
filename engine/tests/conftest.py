import json
import os
import sqlite3

import pytest

from causegraph.ingest import reader
from causegraph.schema import Event

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(REPO_ROOT, "test", "fixtures")


def load_jsonl(name: str) -> list[Event]:
    path = os.path.join(FIXTURES, name)
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Event.from_dict(json.loads(line)))
    return out


@pytest.fixture
def graph_events() -> list[Event]:
    return load_jsonl("graph_events.jsonl")


@pytest.fixture
def graph_db(tmp_path, graph_events) -> str:
    db = str(tmp_path / "graph.db")
    conn = sqlite3.connect(db)
    try:
        reader.write_events(conn, graph_events)
    finally:
        conn.close()
    return db


@pytest.fixture
def why_events() -> list[Event]:
    return load_jsonl("why_events.jsonl")


@pytest.fixture
def why_db(tmp_path, why_events) -> str:
    db = str(tmp_path / "why.db")
    conn = sqlite3.connect(db)
    try:
        reader.write_events(conn, why_events)
    finally:
        conn.close()
    return db


@pytest.fixture
def filewatch_events() -> list[Event]:
    return load_jsonl("filewatch_events.jsonl")


@pytest.fixture
def filewatch_db(tmp_path, filewatch_events) -> str:
    db = str(tmp_path / "fw.db")
    conn = sqlite3.connect(db)
    try:
        reader.write_events(conn, filewatch_events)
    finally:
        conn.close()
    return db
