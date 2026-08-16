# causegraph-M3f-M4 — file capture + inferred file→process causality

> Contract. Adds fsnotify file capture (testable on macOS, no root/entitlement) and the M4 core
> it unblocks: FILE nodes + `file_watch` inferred edges (<1.0) + `scoring.py` + precision
> traversal. This is the first sub-1.0 edge in the system. Divergences tagged `[A]`/`[D]`.

## Goal

Capture `file.change` events and answer "why did this process start?" with a likely file cause —
e.g. `cg why "pid 900"` → "python3 started; possibly triggered by a change to /etc/app.conf
(confidence 0.72)". Reasoning stays deterministic in the graph; the narrator only phrases.

## Out of scope

- `socket`/`cron` rules (no data). Native process attribution for file *writers* (writer→reader
  process edges — needs M3-full). Recursive auto-watch of dirs created after startup. Real LLM.

## Shape

```text
 DAEMON (cged)                                 ENGINE
 generic.Poller[R] --process/resource--\
                                        >-- out[R] -> pipeline[R] -> SQLite[R]
 filewatch.Watcher * --file.change-----/            (2 collectors, 1 channel)
   fsnotify; source=fsnotify;                          |
   actor=UNKNOWN sentinel; target.path                 v reader[R]
                                            builder[R] --> nx.DiGraph
                                              |  process nodes (pid,spawn_ts) kind=process[R]
                                              |  + FILE nodes ("file",path) kind=file *
                                              v  runs registered rules[R]
   spawn rule[R] (1.0) ]------------------> edges
   file_watch rule * (file->process, <1.0 via scoring.py *) 
                                              |
  cg why "<q>" -> attribution.annotate[R]* (skips FILE nodes) -> resolver *
                    (explicit-pid ok even if peak=None when causes exist)
                    -> culprit -> traverse.causes() * (>= min_confidence)
                                              -> narrator.explain(..., causes=) *  (additive kwarg)
```

## Precedent

- `[R]` Collector seam, run() multi-collector, schema/codegen, EdgeRule seam, builder/traverse/
  attribution/narrator, metrics — see `.evidence/precedent.md`. fsnotify v1.8.0 verified there.
- `[A]` file.change carries an UNKNOWN actor sentinel (pid 0) — fsnotify has no attribution; builder
  ignores file.change for process segmentation — `[D]` from doc schema; reversible: native fills it.
- `[A]` file_watch links FILE→process, not writer→reader (no writer identity offline) — reversible.
- `[A]` provenance weights native 1.0 > fsnotify 0.9 > poll 0.8 — honest first ordering, tunable.
- `[P]` node-type ownership (builder owns kind→node; rules propose edges only), single key owner
  (`model.file_key`/`is_process_key`), and edge provenance = min of endpoints — `docs/adr/0001`.

## Unknowns → spikes

- none — fsnotify API verified at source; the rest extends proven seams.

## Acceptance criteria

1. `filewatch.Watcher` implements `collector.Collector`; `-watch <dir1,dir2>` (comma-separated,
   recursive add at start) emits `file.change` events (source=`fsnotify`, `target.path` set,
   actor=UNKNOWN sentinel pid 0); `Caps.FileEvents=true`. A nonexistent `-watch` dir logs and is
   skipped, no crash.
2. Schema `kind` enum gains `file.change` AND `source` enum gains `fsnotify`
   (`shared/schema/event.schema.json`); `make gen` regenerates both files; drift gate passes;
   existing events still validate; codec round-trips a `file.change` event. `event.go` `Validate()`
   also rejects a `Target.Path` that is non-UTF8 or exceeds a new `MaxPathBytes` cap (mirroring
   `MaxArgsBytes`), since file events are the first to populate it.
3. `cged` `run()` starts BOTH the poller and the watcher into one `out` channel, wrapped in a
   `sync.WaitGroup`; `out` is closed exactly once after `wg.Wait()` (never by either collector
   goroutine), so no double-close panic; both stop cleanly on SIGINT; `run()` exits within the 5s guard.
4. Builder owns `kind→node` (ADR 0001): it skips `kind==file.change` in `_segment`'s by-pid grouping
   (so the pid-0 sentinel never creates a process node) and builds FILE nodes in a separate pass — one
   per distinct path via `model.file_key(path)` (key `("file", path)`), `kind="file"`, sorted
   change-ts list. Process nodes get an explicit `kind="process"` attr; otherwise unchanged. Edge
   rules propose edges only (no node creation), so the §6 rule seam is intact.
4b. `attribution.annotate`/`rank_by` route through the single `model.process_keys(g)`/
   `is_process_key` helper (the one owner of the key-type guard) so FILE keys `(str,str)` never enter
   comparisons against process keys `(int,int)` (TypeError). `traverse.parent_of` uses the stronger
   SEMANTIC guard `rule=="spawn"` (a file_watch predecessor is not lineage regardless of key type).
   A golden test runs `cg why` against a DB containing file.change events.
5. `scoring.combine(base, provenance_source, dt, half_life) -> float` in [0,1], monotonic (↑base,
   native≥fsnotify≥poll provenance, smaller dt → higher). For an edge spanning two events the caller
   passes `provenance = min(file.source, spawn.source)` (an edge is only as trustworthy as its weaker
   end — ADR 0001); `half_life` default 60s (> the 2s poll interval, since `dt` is poll-grained). The
   certain `spawn` edge is never rescored (stays 1.0).
6. `file_watch` EdgeRule proposes a `file→process` edge when a process spawns within `window`
   (default 30s, configurable) after a change to a file it references, confidence = `scoring.combine`
   (<1.0); a change >window before the spawn proposes nothing. Path match compares (via
   `os.path.normpath`) `target.path` against `actor.exe` and only the **absolute** `actor.args`
   entries (`os.path.isabs`) — relative args are skipped because no `actor.cwd` is captured, so
   resolving them against the engine's cwd would be wrong. `[A]` reversible: capture `actor.cwd` in a
   later schema rev to match relative args. The rule uses `model.file_key(path)` for its edge parent
   and passes `min(fsnotify, spawn.source)` provenance to `scoring.combine`. The builder runs it as a
   registered rule (edges only; additive — ADR 0001).
7. `traverse.causes(g, key, min_confidence=0.5)` returns incoming file_watch edges (file, confidence)
   ≥ min_confidence; `cg why` shows the culprit's file cause(s); `--all` (min 0) / `--min-confidence X`
   adjust. `cg tree`/`cg path` are unaffected (process lineage only).
7b. `cg why "pid N"` works for a file-caused process that has NO resource sample: the explicit-pid
   resolver path returns ok with `value=None` (it does not exit-1 on a None peak); `cmd_why` exits 1
   only when `value is None AND causes is empty`. (The keyword/rank path still exit-1s on no resource
   data — a resource question with no resource answer.)
8. `narrator.explain(..., causes=None)` (additive kwarg): when `value is None` it omits the peak line
   and leads with the process; per cause it emits "Possibly triggered by a recent change to <path>
   (confidence X)"; deterministic; resolver prints no text.
9. `traverse.parent_of` restricts candidate predecessors to `rule=="spawn"` (process) edges before
   taking max-confidence, so a `file_watch` predecessor is NEVER returned as lineage (else a FILE key
   would enter `ancestry_path` and crash `_summary`'s pid/exe indexing). `cg tree`/`cg path` output
   byte-identical to the M0–M2 baselines; a new golden runs `cg path`/`cg why` on a root process (no
   spawn parent) with an incoming file_watch edge and asserts no FILE key appears in the ancestry chain.
10. `make test` green incl. new tests; live: `cged -watch <dir>`, change a referenced file, spawn a
    process referencing it → `cg why` shows the file cause.

## Test plan

| Dimension | Proven by | Gate |
|---|---|---|
| correctness | table tests: scoring monotonic+clamp+spawn-not-rescored; file_watch (path-in-args + within-window + dt→confidence + no-match); builder file nodes + no process node from file.change; traverse.causes min_confidence filter; narrator causes golden; codec round-trips file.change | must pass |
| reliability | watcher on a nonexistent dir → logged, skipped, no crash (tested); SIGINT stops both collectors and flushes (tested); fsnotify Errors channel is drained in the select loop (log-and-continue) — inspection-verified, since runtime fsnotify errors can't be injected deterministically in-process | must pass |
| concurrency | `go test -race`: poller + filewatch both feeding one `out` channel + pipeline drain | must pass |
| scale | file_watch is O(spawns × referenced-paths); measure added cost at N file nodes / M spawns via a bench, report p50/p95/p99 | report |
| security | `-watch` only watches user-given dirs; `target.path` stored via parameterized SQL; non-UTF8/oversized path rejected by codec; question tokenized only | must pass |
| regression | `cg tree`/`cg path` goldens byte-identical (M0–M2 baselines); the 56 prior tests stay green; new `cg why`-with-cause golden via `cli` collector | must pass |

## Must not regress

- M0–M2 ancestry goldens (`.evidence/baseline/cli/{tree,path}.txt`) byte-identical.
- Existing collectors/pipeline/store behavior — proven by the unchanged Go suite under `-race`.
- The offline query vertical — `cg why` CPU/memory goldens still pass.

## Evidence plan

- `cli` collector — add a `cg why` golden that includes a file cause, against a new fixture.
- `.evidence/artifacts/` — file_watch scale bench (p50/p95/p99).
- Live capture transcript: touch a referenced file → spawn → `cg why` names the file.

## Rollback

- How: additive — new daemon collector, new engine modules, enum value, new fixture. Revert commits.
- Migration reversible: yes — `source=fsnotify`/`kind=file.change` are additive enum values; old DBs
  (schema_version 1) still read; no column/table change. Verified by reading an M0–M2-only DB with the
  new reader (`test_backward_compat_reads_m0m2_only_db`). Reverse direction (rollback with in-flight
  data): a reverted M4-M5 engine has no file.change skip, so it ingests each pid-0 sentinel as an
  inert orphan process node — harmless (never surfaced unless pid 0 is queried) and evicted with the
  ring buffer. Acceptable, hence no schema_version bump.

## Open risks I'm accepting

- No process attribution on file events (fsnotify limit) → file_watch is file→process by path+time.
  Precision comes primarily from AC6's exe/absolute-args **path-reference match**, NOT from
  min_confidence(0.5): the latter only drops weak (distant/low-provenance) edges, while a coincidental
  reference to a recently-changed file can still score >0.5 (ADR 0001). This is the correlation-vs-
  causation risk §5.3 names; the path match is the guard, min_confidence is a secondary filter.
- fsnotify is not recursive; only dirs present at startup are watched (documented).
