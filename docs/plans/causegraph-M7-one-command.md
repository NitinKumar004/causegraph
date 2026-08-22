# causegraph-M7 — one-command, always-on local use

> Contract. Make CauseGraph usable by another person without babysitting a terminal:
> a single `cg up` that runs a background recorder (survives the shell) and serves the
> live UI, plus `cg down` / `cg status`. No root, no approvals. Divergences tagged `[A]`.

## Goal

Today a user must run two commands (recorder + UI), the recorder freezes the moment they
Ctrl-C it, and the DB path is manual. That's a developer dance, not a product. Give it a
front door: `cg up` (record in the background + open the UI), `cg down`, `cg status`.

## Shape

```
  cg up ─┬─▶ service.start(db)  ──▶ detached cged (start_new_session) ──▶ ~/.causegraph/live.db *
         │        └ pidfile ~/.causegraph/cged.pid *                        (survives the shell)
         └─▶ server.serve(db) ──▶ UI @127.0.0.1:8765 (foreground; Ctrl-C = UI only) *
  cg down  ──▶ SIGTERM the pidfile's pid, clear it *
  cg status ─▶ pid alive? + COUNT/MAX(ts) from the DB → live | frozen *
```

- `[A]` **Detached background recorder, not a launchd/systemd service.** `start_new_session=True`
  + a pidfile is cross-platform, testable, and doesn't touch the user's system daemons. Auto-start-
  on-login (launchd/systemd) is a documented follow-up, deliberately not auto-installed (invasive,
  needs its own consent + test). Reversible: it's just a subprocess.
- `[A]` **Stable data dir `~/.causegraph/`** (`$CAUSEGRAPH_HOME` override): `live.db`, `cged.pid`,
  `cged.log`. So `cg up`/`status`/`down` share state with no flags.
- `[A]` **`cg up` samples everything, adaptive poll, runs forever** (`-sample-min-cpu/rss 0`,
  `-duration 0`) — the "show me my machine" default. Ctrl-C stops only the UI; the recorder persists.

## Config / discovery

`find_cged()`: `$CAUSEGRAPH_CGED` → repo `bin/cged` → `PATH`. DB defaults to `~/.causegraph/live.db`.

## Acceptance criteria

- `cg up` starts a recorder that keeps writing after the launching shell exits, and serves the UI.
- `cg up` is idempotent — a second `up` reuses the running recorder, never spawns a duplicate.
- `cg status` reports running/stopped + event count + newest-event age (live vs frozen).
- `cg down` stops the recorder and clears the pidfile; a stale pidfile never reads as "running".

## Test plan

| dimension | mechanism |
|---|---|
| correctness | `test_service.py`: start→pidfile+alive, idempotent second start, stop→killed+cleared, dead-pid pidfile ≠ running (fake `cged` = a sleeper, no real capture) |
| reliability | `find_cged` precedence (env override); `status` shape when DB absent |
| concurrency | one detached child; liveness via `kill(pid,0)` — no shared mutable state |
| scale | recorder is `-duration 0` with the existing `-max-rows` ring cap so the DB can't grow unbounded |
| security | loopback UI unchanged; no new privilege; data dir under `$HOME` |
| regression | existing `cg ui/tree/path/load` untouched; full `make test` green |

## Unknowns → spikes

`none` — detached subprocess + pidfile is standard; verified live (`cg up`→`status`→`down`) on macOS.

## Open risks I'm accepting

- **POSIX-only stop** (SIGTERM). Windows would need a different path — consistent with the repo's
  macOS/Linux verification scope. `[unresolved]`
- **No auto-start on login yet** — the recorder stops on reboot/logout until launchd/systemd
  integration lands (follow-up). `cg up` re-arms it in one command.
