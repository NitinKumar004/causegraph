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
- CLI `cg tree|path|ui|load` → `engine/causegraph/cli.py`; wrapper `scripts/cg`. No NL/`cg why` command — natural-language query + the LLM/narrator/resolver layer were removed (graph-only product). confidence: high
- Scale harness → `daemon/cmd/scalebench/main.go` (write path) + `scripts/scale_query.py` (DAG build/traverse/RSS/retention); `make scale`. confidence: high
- Resource attribution (M4) → `engine/causegraph/graph/attribution.py`: `annotate(g, events)` matches resource.samples to node instances by `[spawn_ts,exit_ts)` window; `rank_by(g, metric)` None-safe. Metric keys in `engine/causegraph/metrics.py` (CPU/RSS). confidence: high
- Query/LLM layer — REMOVED. The old `engine/causegraph/query/` (resolver + narrator + llm adapter) and the `?q=`/`cg why` natural-language path are gone; the graph is the whole product. The UI ranks the hottest process itself. confidence: high
- Web UI (M6, read-only) → `engine/causegraph/api/server.py`: pure `graph_payload(db,pid,min_confidence,max_nodes,show_all,family)` serializes the culprit neighborhood (bounded on both axes, `truncated` flag) + `ThreadingHTTPServer` bound to 127.0.0.1 (db fixed at startup, allowlist static routes; guarded POST `/api/kill`). Page: `ui/index.html`+`app.js`+vendored `ui/vendor/cytoscape.min.js`. Launch `cg ui`. BFS walk = `traverse.descendants_bfs`. confidence: high
- Build/test entrypoint → `Makefile` (`make test` = check-gen + go race + pytest; `make fixtures` rebuilds clean — rm DBs first, since `cg load` appends; `make scale` → `.evidence/artifacts`). confidence: high
- File capture (M3f) → `daemon/internal/collector/filewatch/watch.go`: fsnotify Collector (no root), emits `file.change` with `target.path` + UNKNOWN pid-0 actor; `-watch` dirs; `cged` run() fans in multiple collectors via WaitGroup (one channel close). confidence: high
- M4 core (SHIPPED, first <1.0 edge) → `engine/causegraph/graph/scoring.py` (base×provenance×temporal, `min_source`); `graph/edges/file_watch.py` (file→process, path-match precision); FILE nodes via `model.file_key` (normalizes; single key owner) + `model.process_keys`/`is_process_key`; `graph/traverse.py:causes()` + `parent_of` spawn-only; the UI shows the file cause, `min_confidence` gates it. See ADR `docs/adr/0001` + `docs/plans/causegraph-M3f-M4.md`. confidence: high
- STILL DEFERRED (needs native process attribution / more data): `socket`/`cron` edge rules, writer→reader process edges, native backends (M3-full eBPF/ETW/ES), fleet. (LLM/NL query intentionally dropped — not deferred.)

STALE-RISK: binding scale limit — engine rebuilds the whole networkx DAG per query (1M rows ≈ 7.6s / 2.3 GiB, measured 2026-08-16 `.evidence/artifacts/scale_query.json`). Fine for M2 scope (hours on one machine); revisit (caching/incremental build) before large windows or fleet.
