"""Local service management: run the recorder (`cged`) as a detached background
process so capture keeps going without a babysat terminal, and manage its lifecycle
(start / stop / status) plus a stable data directory.

State lives under ~/.causegraph (override with $CAUSEGRAPH_HOME): the live rolling
DB, a pidfile, and the recorder's log. POSIX only for the stop path (SIGTERM);
Windows would need a different mechanism.
"""
from __future__ import annotations

import os
import shutil
import signal
import sqlite3
import subprocess
import time


def data_dir() -> str:
    """The stable state directory, created on demand."""
    d = os.environ.get("CAUSEGRAPH_HOME") or os.path.join(os.path.expanduser("~"), ".causegraph")
    os.makedirs(d, exist_ok=True)
    return d


def default_db() -> str:
    return os.path.join(data_dir(), "live.db")


def _pidfile() -> str:
    return os.path.join(data_dir(), "cged.pid")


def _logfile() -> str:
    return os.path.join(data_dir(), "cged.log")


def find_cged() -> str | None:
    """Locate the recorder binary: explicit $CAUSEGRAPH_CGED, then the repo's bin/cged
    (relative to this package), then whatever is on PATH."""
    env = os.environ.get("CAUSEGRAPH_CGED")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    here = os.path.dirname(os.path.abspath(__file__))          # engine/causegraph
    repo = os.path.dirname(os.path.dirname(here))              # repo root
    cand = os.path.join(repo, "bin", "cged")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which("cged")


def _read_pid() -> int | None:
    try:
        with open(_pidfile(), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, kills nothing
    except OSError:
        return False
    return True


def running_pid() -> int | None:
    """The live recorder pid, or None (also clears a stale pidfile)."""
    pid = _read_pid()
    if _alive(pid):
        return pid
    return None


def start(db: str, extra_args: list[str] | None = None):
    """Spawn a detached recorder writing to db (samples ALL processes, adaptive poll,
    runs until stopped). Idempotent: returns ("already", pid) if one is live."""
    pid = running_pid()
    if pid:
        return ("already", pid)
    cged = find_cged()
    if not cged:
        raise FileNotFoundError("cged binary not found — run `make build` first, or set $CAUSEGRAPH_CGED")
    args = [cged, "-db", db, "-sample-min-cpu", "0", "-sample-min-rss", "0", "-duration", "0"]
    if extra_args:
        args += extra_args
    log = open(_logfile(), "ab")  # noqa: SIM115 — handed to the child; closed when it exits
    # start_new_session detaches from our process group so the recorder outlives the
    # shell that launched it — the whole point of "always-on".
    proc = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    with open(_pidfile(), "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    return ("started", proc.pid)


def stop(timeout_s: float = 5.0):
    """SIGTERM the recorder and wait briefly for it to exit; clears the pidfile."""
    pid = running_pid()
    if not pid:
        try:
            os.remove(_pidfile())
        except OSError:
            pass
        return ("not-running", None)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    try:
        os.remove(_pidfile())
    except OSError:
        pass
    return ("stopped", pid)


def status(db: str) -> dict:
    """Recorder liveness + store freshness, for `cg status` and a human check."""
    info = {"recorder_pid": running_pid(), "db": db, "events": None, "latest_age_s": None}
    try:
        conn = sqlite3.connect(db)
        try:
            count, latest = conn.execute("SELECT COUNT(*), MAX(ts) FROM events").fetchone()
        finally:
            conn.close()
        info["events"] = count
        if latest:
            info["latest_age_s"] = max(0.0, time.time() - latest / 1e9)
    except sqlite3.Error:
        pass
    return info
