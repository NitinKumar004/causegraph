# map

Built and maintained by `/ship-harness:memory` — which `ship` and `review` run for you
when this file needs it. `ship` (S7) and `review` (R8) add lines as they learn.

Every line cites a path and is re-checkable by grep. This file is **derived and
disposable** — if it ever goes weird, delete it and run the backfill again. Nothing
may live only here.

- Canonical event schema (single source of truth) → `shared/schema/event.schema.json`; codegen `shared/schema/gen.py` emits `daemon/internal/event/event_gen.go` + `engine/causegraph/schema/event_gen.py`. seen: 3 files · since M0 2026-08 · verified 2026-08-16 · confidence: high
- Capture daemon (Go) `cged` → `daemon/cmd/cged/main.go` (testable `run()`); wiring config→collector→pipeline→store. verified 2026-08-16 · confidence: high
- Capture seam: `collector.Collector` interface (leaf pkg) `daemon/internal/collector/collector.go`; generic gopsutil backend `daemon/internal/collector/generic/poll.go` (+ pure `diff.go`). Native backends (M3) add one file. confidence: high
- Pipeline (drop-OLDEST ring buffer + batched drain + `dropped_overflow`/`dropped_write_error`) → `daemon/internal/pipeline/{buffer,pipeline}.go`. confidence: high
- Store seam: `store.Sink` `daemon/internal/store/store.go`; default WAL SQLite ring buffer `daemon/internal/store/sqlite.go` (atomic insert+evict, `meta` schema_version). confidence: high
- Engine (Python): read `engine/causegraph/ingest/reader.py` → build networkx DAG `engine/causegraph/graph/builder.py` (nodes keyed (pid,spawn_ts)) → traverse `graph/traverse.py`. confidence: high
- EdgeRule plugin seam (the extensibility heart) → `engine/causegraph/graph/edges/base.py` (`BuildContext` carries raw events = the doc's EventWindow) + `edges/spawn.py` (ppid, conf 1.0). M4 rules add files here. confidence: high
- CLI `cg tree|path|load` → `engine/causegraph/cli.py`; wrapper `scripts/cg`. `cg why` is reserved for M5, deliberately absent. confidence: high
- Scale harness → `daemon/cmd/scalebench/main.go` (write path) + `scripts/scale_query.py` (DAG build/traverse/RSS/retention); `make scale`. confidence: high
- Build/test entrypoint → `Makefile` (`make test` = check-gen + go race + pytest incl. seam integration). confidence: high

STALE-RISK: binding scale limit — engine rebuilds the whole networkx DAG per query (1M rows ≈ 7.6s / 2.3 GiB, measured 2026-08-16 `.evidence/artifacts/scale_query.json`). Fine for M2 scope (hours on one machine); revisit (caching/incremental build) before large windows or fleet.
