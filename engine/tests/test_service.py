"""service.py: data dir, cged discovery, and the detached-recorder lifecycle
(start writes a pidfile for a live process; stop kills it and clears the file).
Uses a fake 'cged' (a tiny sleeper) so no real capture runs."""
import os
import stat
import time

import pytest

from causegraph import service


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUSEGRAPH_HOME", str(tmp_path))
    monkeypatch.delenv("CAUSEGRAPH_CGED", raising=False)
    return tmp_path


def _fake_cged(tmp_path):
    """A stand-in binary that ignores cged's flags and just sleeps — lets us test the
    process lifecycle without running the real recorder."""
    p = tmp_path / "fake_cged"
    p.write_text('#!/bin/sh\nexec sleep 30\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def test_data_dir_honours_env(home):
    assert service.data_dir() == str(home)
    assert service.default_db() == os.path.join(str(home), "live.db")


def test_find_cged_env_override(home, tmp_path):
    fake = _fake_cged(tmp_path)
    os.environ["CAUSEGRAPH_CGED"] = fake
    try:
        assert service.find_cged() == fake
    finally:
        del os.environ["CAUSEGRAPH_CGED"]


def test_start_stop_lifecycle(home, tmp_path, monkeypatch):
    monkeypatch.setenv("CAUSEGRAPH_CGED", _fake_cged(tmp_path))
    db = service.default_db()

    state, pid = service.start(db)
    assert state == "started" and service.running_pid() == pid

    # idempotent: a second start doesn't spawn a duplicate
    state2, pid2 = service.start(db)
    assert state2 == "already" and pid2 == pid

    state3, pid3 = service.stop()
    assert state3 == "stopped" and pid3 == pid
    assert service.running_pid() is None
    assert not os.path.exists(os.path.join(str(home), "cged.pid"))


def test_stop_when_not_running(home):
    assert service.stop()[0] == "not-running"


def test_running_pid_ignores_dead_pidfile(home):
    # a pidfile pointing at a long-dead pid must not read as "running"
    with open(os.path.join(str(home), "cged.pid"), "w", encoding="utf-8") as f:
        f.write("2147480000")  # impossibly high, not a live process
    assert service.running_pid() is None


def test_status_shape(home):
    s = service.status(service.default_db())  # db doesn't exist yet
    assert s["recorder_pid"] is None and s["events"] is None
