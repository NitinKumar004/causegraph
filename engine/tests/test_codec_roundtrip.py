"""AC2: the canonical event round-trips losslessly in Python, and the shared
fixture decodes to the SAME values Go asserts (cross-language contract)."""
from conftest import load_jsonl

from causegraph.schema import (
    KIND_HEARTBEAT,
    KIND_PROCESS_SPAWN,
    KIND_RESOURCE_SAMPLE,
    SOURCE_POLL,
    Event,
)


def test_roundtrip_lossless():
    events = load_jsonl("events.jsonl")
    assert len(events) == 4
    for e in events:
        again = Event.from_dict(e.to_dict())
        assert again == e


def test_fixture_exact_values():
    """Must match daemon/internal/event/codec_test.go:TestFixtureExactValues."""
    events = load_jsonl("events.jsonl")

    hb = events[0]
    assert hb.kind == KIND_HEARTBEAT
    assert hb.actor.pid == 42 and hb.actor.ppid == 1
    assert hb.actor.exe == "/usr/bin/cged" and hb.actor.args == []
    assert hb.source == SOURCE_POLL and hb.confidence == 1.0
    assert hb.target is None and hb.metrics is None

    spawn = events[1]
    assert spawn.kind == KIND_PROCESS_SPAWN
    assert spawn.actor.pid == 100 and spawn.actor.ppid == 42
    assert spawn.actor.args == ["bash", "-c", "sleep 1"]
    assert spawn.actor.user == "alice"

    rs = events[3]
    assert rs.kind == KIND_RESOURCE_SAMPLE
    assert rs.metrics is not None
    assert rs.metrics.cpu_pct == 12.5
    assert rs.metrics.rss_bytes == 1048576
    assert rs.metrics.temp_c is None


def test_to_dict_omits_absent_optionals():
    events = load_jsonl("events.jsonl")
    hb = events[0].to_dict()
    assert "target" not in hb and "metrics" not in hb
    rs = events[3].to_dict()
    assert "temp_c" not in rs["metrics"]  # absent optional omitted
