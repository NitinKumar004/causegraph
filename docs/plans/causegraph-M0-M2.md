# causegraph-M0-M2 — CauseGraph vertical slice: schema→capture→ancestry CLI

> Contract. Scoped to M0–M2 of `docs/architecture.md`. Faithful to that doc; divergences
> tagged `[D]`/`[A]`. A drift-check agent verifies the build against the Acceptance criteria.

## Goal

A cross-platform tool where the Go daemon `cged` captures process spawn/exit + resource
samples as canonical Events into SQLite, and `cg tree <pid>` / `cg path <pid>` print the
process-ancestry causal tree from those events — on macOS/Windows/Linux, no root/entitlements.

## Out of scope

- Native backends: eBPF / ETW / EndpointSecurity (M3, M7). Generic gopsutil only.
- Inferred edges + confidence scoring: file_watch/socket/cron/resource rules (M4).
- Natural-language / LLM query, resolver, narrator (M5). Web UI (M6). Fleet/remote sink.
- File/socket events. M1 captures process + resource kinds only (gopsutil floor).

## Shape

```text
  DAEMON (Go, cged)                         ENGINE (Python, cg)
+----------------+  Event   +-----------+
| Collector *    |--------->| pipeline *|   ring buf, drop-OLDEST + counter
|  generic/poll  |  chan    +-----------+
+----------------+               | batch
   ^ snapshot diff               v
   process list       +------------------+ rows  +-----------+  DAG   +--------+
                      | store *          |------>| reader *  |------->|builder*|
                      | sqlite WAL       |       | ingest    |        |networkx|
                      | row-cap, evict-  |       +-----------+        +--------+
                      | oldest, meta ver |                       builder runs EdgeRule(s)
                      | (via Sink iface) |                       M2 = ppid rule | edges
                      +------------------+                                       v
        ^ shared contract                                          +-----------+
        |                                                          | cg CLI *  |
   shared/schema/event.schema.json *                               | tree/path |
        |  gen.py                                                   +-----------+
        +--> daemon/internal/event/event_gen.go * (pkg event)
        +--> engine/causegraph/schema/event_gen.py * (dataclass)
```

## Precedent

- `[N]` whole system — greenfield, no internal precedent (`.evidence/precedent.md`).
- `[P]` schema is single source of truth; codegen Go+Py — `docs/architecture.md` §3.3.
- `[P]` drop-OLDEST backpressure, batched WAL writes, coarse resource sampling — §5.1.
- `[P]` certain edges only (ppid, conf 1.0) for ancestry; networkx in-memory — §5.3, M2.
- `[A]` SQLite driver = `modernc.org/sqlite` v1.34.4 (pure-Go) — because cgo-free keeps the
  daemon cross-compilable; doc reserves cgo for macOS ES only — reversible: swap store driver.
- `[A]` gopsutil pinned v4.25.6; args via `CmdlineSlice()`; ts = CreateTime ms ×1e6 → ns —
  verified at source — wrong if API shifts — reversible: adjust `generic/poll.go`.
- `[A]` codegen = `shared/schema/gen.py` emitting Go into `daemon/internal/event/event_gen.go`
  (pkg `event`) and Py into `engine/causegraph/schema/event_gen.py` — generated INTO each
  consumer (not the doc's single `shared/schema/gen/`) for Go/Python module-boundary reasons;
  JSON schema stays the sole source of truth — `[D]` from `docs/architecture.md` §4 layout;
  blast radius: two generated files only — reversible: change output paths in gen.py.
- `[D]` M2 command is `cg path <pid>` (root-ward path), NOT `cg why` — reserves `cg why
  "<question>"` for M5 NL query (§5.4) to avoid a later breaking rename; `cg tree <pid>` stays.

## Unknowns → spikes

- none — API surface verified at source in S0; mechanisms are standard.

## Acceptance criteria

0. The source architecture doc is committed unchanged to `docs/architecture.md` at M0 (all
   `[P]`/`[D]` citations and the drift-check agent resolve against that path).
1. `event.schema.json` is the only hand-written event definition; `make gen` regenerates
   `daemon/internal/event/event_gen.go` + `engine/causegraph/schema/event_gen.py`, and a drift
   test fails (`git diff --exit-code` on those paths after regen) if either is stale.
2. Schema `kind` enum includes `heartbeat` (added at M0), plus `process.spawn|process.exit|
   resource.sample`; codec round-trips every canonical Event (Go encode → Py decode and Py→Go)
   losslessly, including a `heartbeat` event. A heartbeat sets actor.pid/ppid to cged's own
   pid/ppid, exe to the cged binary path, args=[], user to the daemon's running user;
   target/metrics omitted. Every M0–M2 event sets `source="poll"`, `confidence=1.0`, and
   `host_id=os.Hostname()`; the round-trip test asserts these exact values (M4 scoring depends
   on `source`/`confidence` being present and correct, §5.3).
3. Store: WAL enabled; row count capped at `config.MaxRows` (default 1_000_000); batched insert
   (one txn per batch) and the delete-oldest-past-cap runs in the SAME transaction as that
   insert (atomic — a kill -9 between batches leaves rows ≤ MaxRows on reopen); a `meta` table
   row `schema_version` written at DB creation and readable by the engine.
4. Pipeline buffer holds `config.BufferSize` (default 8192); when full it drops the OLDEST
   event (never newest) and increments `dropped_overflow`; collector never blocks on a stalled
   store.
5. A single poll tick, run every `config.SampleInterval` (default 2s), both diffs current vs
   previous snapshot for spawn (new pids) / exit (gone pids), handling pid reuse via create-time,
   AND emits a resource.sample only for "processes that matter" (§5.1 r3): pids with
   `cpu_pct >= config.SampleMinCPUPct` (default 1.0) OR `rss_bytes >= config.SampleMinRSS`
   (default 100MiB) — testable by feeding snapshots with known cpu/rss. One config governs cadence.
6. `cged` main emits a `heartbeat` every `config.HeartbeatInterval` (default 30s) — bounded, one
   row per tick — and writes real process events to SQLite; exits cleanly on SIGINT.
7. Builder keys graph nodes by `(pid, create-time)` — NOT pid alone — so a reused pid over the
   MaxRows window is two distinct nodes; each ppid edge resolves to the parent live at the
   child's spawn ts. The ppid edge is produced by ONE registered `EdgeRule` (§5.3/§6): the
   builder runs every registered rule, so M4 adds rule files with no builder change.
   `cg tree <pid>` prints the ancestry subtree; `cg path <pid>` prints the root-ward ancestry
   path; both deterministic from a committed fixture DB.
8. On a store write error the pipeline does not retry: it logs, counts the failed batch's events
   into `dropped_write_error` (distinct from `dropped_overflow` so "store slow" ≠ "store broken"),
   and continues draining subsequent batches.
9. `generic/poll` is constructed and used ONLY behind the `Collector` interface (Start/Caps/Close,
   §3.1); `cged` main wires the collector solely via that interface, so M3 native backends add
   one file with nothing downstream changed.
10. The pipeline writes through a `Sink` interface (§5.2/§6); the SQLite store is its default impl,
    so a future remote/fleet sink (deferred) is one implementation, not a rewrite.
11. `make test` runs Go (`-race`) + Python suites green on this host.

## Test plan

| Dimension | Proven by | Gate |
|---|---|---|
| correctness | table tests: snapshot-diff (spawn/exit/pid-reuse), codec round-trip, builder edges from ppid, traverse.py backward walk on fixture; **eviction: fill buffer to BufferSize, push +1 → oldest evicted, newest retained, dropped==1** | must pass |
| reliability | injected: store write returns error → pipeline keeps draining, counts `dropped_write_error`; SIGINT mid-batch flushes then exits; kill -9 between batches → rows ≤ MaxRows on reopen; meta schema_version read back after reopen | must pass |
| concurrency | `go test -race`: N=8 producers into ring buffer + 1 batch drainer against shared buffer+counter; **WAL live-read: a reader SELECTs the live DB while a writer holds an open batch-insert txn on the same file → no `database is locked`** | must pass |
| scale (write) | feed 100k events through pipeline→sqlite; report p50/p95/p99 write-batch latency + throughput + drop rate | gate: drop==0 when BufferSize ≥ inflight; p99 reported (fail only on drop>0) |
| scale (query) | build networkx DAG + run `cg tree`/`cg path` against a MaxRows-sized (1M-row) fixture DB; report build+traverse p50/p95/p99 + peak RSS | gate: report; the binding limit (per-query O(rows) build) is named + numbers captured to `.evidence/artifacts/` |
| scale (retention) | at 10× live-pid count, measure effective window (oldest→newest ts) under MaxRows | report: confirms sampling-that-matters keeps a usable window |
| security | reader uses parameterized SQL only; malformed/oversized args + non-UTF8 exe in event → codec rejects, no panic; CLI pid arg validated int | must pass |
| regression | golden: `cg --help` and `cg tree <fixture-pid>` stable output via the `cli` collector | must pass |

## Must not regress

- The seam: daemon-written DB is readable by engine unchanged — proven by integration test
  `test/` replaying a committed fixture event stream through reader→builder→CLI.
- Schema/codegen drift — proven by the generated-file staleness test (criterion 1).

## Evidence plan

- `tests` collector — `make test` full output (Go -race + pytest).
- `cli` collector — golden `cg --help` and `cg tree` against fixture DB.
- `.evidence/artifacts/` — scale-run tuple (p50/p95/p99, throughput, drop rate) for 100k events;
  before/after: raw fixture events vs rendered ancestry tree.

## Rollback

- How: greenfield; revert the branch / delete generated artifacts. No data migration, no prod.
- Migration reversible: yes — SQLite file is disposable; schema versioned in a `meta` table so
  a future schema change is additive, verified by opening a v1 DB with current reader in a test.

## Open risks I'm accepting

- gopsutil misses processes shorter-lived than the poll interval (doc §3.2 accepts this as the
  fidelity floor); native backends (M3) close it later — documented, not fixed here.
- CPUPercent first sample per process is 0/coarse (gopsutil needs two reads); acceptable for
  sampling semantics — noted in a code comment in `daemon/internal/collector/generic/poll.go`.
