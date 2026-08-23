"""Local service management: run the recorder (`cged`) as a detached background
process so capture keeps going without a babysat terminal, and manage its lifecycle
(start / stop / status) plus a stable data directory.

State lives under ~/.causegraph (override with $CAUSEGRAPH_HOME): the live rolling
DB, a pidfile, and the recorder's log. POSIX only for the stop path (SIGTERM);
Windows would need a different mechanism.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

LABEL = "dev.causegraph.recorder"  # launchd label / systemd unit stem


def data_dir() -> str:
    """The stable state directory, created on demand. Mode 0700 so the capture (which can hold
    command lines with secrets), logs, and pidfile aren't readable by other users on the host."""
    d = os.environ.get("CAUSEGRAPH_HOME") or os.path.join(os.path.expanduser("~"), ".causegraph")
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # tighten even if it already existed at a looser mode
    except OSError:
        pass
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


def _recorder_args(cged: str, db: str) -> list[str]:
    """The one true recorder command line, shared by the detached `cg up` path and
    the login-service unit: capture ALL processes, adaptive poll, run until stopped."""
    return [cged, "-db", db, "-sample-min-cpu", "0", "-sample-min-rss", "0", "-duration", "0"]


def start(db: str, extra_args: list[str] | None = None):
    """Spawn a detached recorder writing to db (samples ALL processes, adaptive poll,
    runs until stopped). Idempotent: returns ("already", pid) if one is live."""
    pid = running_pid()
    if pid:
        return ("already", pid)
    cged = find_cged()
    if not cged:
        raise FileNotFoundError("cged binary not found — run `make build` first, or set $CAUSEGRAPH_CGED")
    args = _recorder_args(cged, db)
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


# ---- login services: launchd (macOS) / systemd --user (Linux) ----
# Turnkey = two units: the recorder (always capturing) and the UI (always serving
# 127.0.0.1). When active they are the authority; `cg up` defers to them. No root,
# no signing, no Apple approval — a LaunchAgent/user-unit is a per-user capability.

RECORDER_LABEL = "dev.causegraph.recorder"
UI_LABEL = "dev.causegraph.ui"
LABEL = RECORDER_LABEL  # back-compat with `cg service install`


def service_supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def _short(label: str) -> str:
    return label.rsplit(".", 1)[-1]  # dev.causegraph.recorder -> recorder


def _agent_path(label: str) -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", label + ".plist")


def _unit_name(label: str) -> str:
    return "causegraph-" + _short(label)


def _systemd_path(label: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "systemd", "user", _unit_name(label) + ".service")


def _svc_log(label: str) -> str:
    return os.path.join(data_dir(), _short(label) + ".log")


def service_active(label: str = RECORDER_LABEL) -> bool:
    """Is the named login service loaded/running right now?"""
    if sys.platform == "darwin":
        return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                              capture_output=True).returncode == 0
    if sys.platform.startswith("linux"):
        r = subprocess.run(["systemctl", "--user", "is-active", _unit_name(label)],
                           capture_output=True, text=True)
        return r.stdout.strip() == "active"
    return False


def _pythonpath() -> str:
    """engine (+ vendored deps, in a release archive) — baked into the UI service so
    it doesn't depend on launchd's minimal environment."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    parts = [os.path.join(repo, "engine")]
    vend = os.path.join(repo, "vendor")
    if os.path.isdir(vend):
        parts.append(vend)
    return os.pathsep.join(parts)


def _install_unit(label: str, args: list[str], env: dict | None = None) -> str:
    if sys.platform == "darwin":
        return _install_launchd(label, args, env)
    if sys.platform.startswith("linux"):
        return _install_systemd(label, args, env)
    raise RuntimeError("login services are supported on macOS and Linux only")


def _uninstall_unit(label: str) -> None:
    if sys.platform == "darwin":
        _uninstall_launchd(label)
    elif sys.platform.startswith("linux"):
        _uninstall_systemd(label)


def _install_launchd(label: str, args: list[str], env: dict | None = None) -> str:
    path = _agent_path(label)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plist = {
        "Label": label, "ProgramArguments": args,
        "RunAtLoad": True, "KeepAlive": True,
        "StandardOutPath": _svc_log(label), "StandardErrorPath": _svc_log(label),
    }
    if env:
        plist["EnvironmentVariables"] = env
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", path], capture_output=True, text=True)
    if r.returncode != 0:  # older macOS: fall back to the legacy verb
        subprocess.run(["launchctl", "load", "-w", path], capture_output=True)
    return path


def _uninstall_launchd(label: str) -> None:
    uid = os.getuid()
    path = _agent_path(label)
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    if os.path.exists(path):
        subprocess.run(["launchctl", "unload", "-w", path], capture_output=True)
        os.remove(path)


def _install_systemd(label: str, args: list[str], env: dict | None = None) -> str:
    path = _systemd_path(label)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exec_start = " ".join(shlex.quote(a) for a in args)
    env_lines = "".join(f"Environment={shlex.quote(f'{k}={v}')}\n" for k, v in (env or {}).items())
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Unit]\n"
                f"Description=CauseGraph ({_short(label)})\n"
                "After=default.target\n\n"
                "[Service]\n"
                f"{env_lines}"
                f"ExecStart={exec_start}\n"
                "Restart=always\n\n"
                "[Install]\n"
                "WantedBy=default.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", _unit_name(label)], capture_output=True)
    return path


def _uninstall_systemd(label: str) -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", _unit_name(label)], capture_output=True)
    path = _systemd_path(label)
    if os.path.exists(path):
        os.remove(path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def _recorder_service_args(db: str) -> list[str]:
    cged = find_cged()
    if not cged:
        raise FileNotFoundError("cged binary not found — run `make build` first, or set $CAUSEGRAPH_CGED")
    return _recorder_args(cged, db)


def _ui_service_args(db: str, host: str, port: int) -> list[str]:
    # Bake THIS interpreter (the one running `cg setup`, already verified >=3.10 by
    # scripts/cg) so launchd's minimal PATH can't fall back to macOS's stock 3.9.
    return [sys.executable, "-m", "causegraph.cli", "ui", "--db", db, "--host", host, "--port", str(port)]


def install_service(db: str) -> str:
    """Recorder-only login service (lower-level; `cg service install`)."""
    if not service_supported():
        raise RuntimeError("auto-start on login is supported on macOS and Linux only")
    stop()  # avoid two writers on the DB
    return _install_unit(RECORDER_LABEL, _recorder_service_args(db))


def uninstall_service() -> None:
    _uninstall_unit(RECORDER_LABEL)


def setup(db: str, host: str = "127.0.0.1", port: int = 8765) -> str:
    """Turnkey: install BOTH the recorder and the UI as login services so capture
    and the dashboard are always up. Returns the dashboard URL."""
    if not service_supported():
        raise RuntimeError("cg setup is supported on macOS and Linux only")
    stop()  # stop any detached `cg up` recorder first
    _install_unit(RECORDER_LABEL, _recorder_service_args(db))
    _install_unit(UI_LABEL, _ui_service_args(db, host, port), env={"PYTHONPATH": _pythonpath()})
    return f"http://{host}:{port}"


def teardown() -> None:
    """Remove both login services (kept-on-disk capture is left alone)."""
    _uninstall_unit(UI_LABEL)
    _uninstall_unit(RECORDER_LABEL)

