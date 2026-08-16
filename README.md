# CauseGraph

*A flight recorder for your computer. Ask it why anything is happening; it traces the cause
back to a root you can act on.*

Two processes, one contract: a **Go daemon** (`cged`) captures OS activity into one canonical
event schema and writes it to SQLite; a **Python engine** (`cg`) builds an in-memory causal
graph from those events and answers questions like *"why is the fan loud?"* or *"why did this
process start?"* — with the reasoning kept in the graph, not in a model.

Full design: [`docs/architecture.md`](docs/architecture.md).

---

## Status — what's built vs. what's left

> **Read this first when picking the project back up.** Everything marked ✅ is shipped **and
> tested** (`make test` is green: Go `-race` + pytest, plus deterministic CLI goldens). Each
> milestone has a plan in [`docs/plans/`](docs/plans) and design decisions in
> [`docs/adr/`](docs/adr).

| Milestone | Status | What it delivers | Plan |
|---|---|---|---|
| **M0** — Skeleton + contract | ✅ Done | `event.schema.json` single source of truth → codegen Go+Python; WAL SQLite ring-buffer store behind a `Sink` seam | `docs/plans/causegraph-M0-M2.md` |
| **M1** — Cross-platform capture | ✅ Done | `Collector` seam + generic **gopsutil** backend (process spawn/exit + resource sampling); drop-oldest pipeline. Runs on macOS/Windows/Linux, no root | `causegraph-M0-M2.md` |
| **M2** — Ancestry graph + CLI | ✅ Done | networkx DAG from ppid (certain edges); `cg tree` / `cg path` | `causegraph-M0-M2.md` |
| **M4 (query part) + M5-offline** | ✅ Done | resource attribution + `cg why "<question>"` (resolver → traversal → **deterministic offline narrator**; `query/llm.py` adapter, **no real LLM yet**) | `causegraph-M4-M5.md` |
| **M3f (file capture) + M4 core** | ✅ Done | **fsnotify** `file.change` capture (no root) + the first **inferred (<1.0) edge**: `file_watch` (a file changed → likely triggered a process); `scoring.py`; precision traversal; `cg why` shows the file cause | `causegraph-M3f-M4.md` + `docs/adr/0001` |
| **M3-full** — native backends | ⬜ Left | Real kernel capture: eBPF (Linux), ETW (Windows), EndpointSecurity (macOS). Needs Linux/Windows to build+test; macOS ES needs an **Apple-approved entitlement**. Unlocks process attribution for file/socket events | — |
| **M4 remaining rules** | ⬜ Left | `socket` (IPC), `cron`/temporal, and **writer→reader** file edges — all need native process attribution (M3-full) | — |
| **M5-full** — real LLM | ⬜ Left | A real provider behind `query/llm.py` (currently offline templated narrator). Additive — the seam is ready | — |
| **M6** — web UI | ⬜ Left | Local server + d3/cytoscape causal tree with confidence on edges | — |
| **Fleet** — many machines | ⬜ Left | A remote `Sink` implementation shipping events to a central store keyed by `host_id` | — |

### Smaller enhancements also left
- **`actor.cwd` capture** — so `file_watch` can match *relative*-path args (today it matches only
  `exe` + absolute args). Schema addition.
- **Recursive / auto file-watch** — fsnotify currently watches only directories present at startup.
- **Query-time graph caching** — the engine rebuilds the whole DAG per `cg` call (O(rows): ~2.6s /
  1.6 GiB at 1M rows). Fine for M2 scope; revisit before large windows or fleet.
- **Temperature** — "fan/heat" is currently inferred from sustained **CPU** (no temp sensor via the
  generic backend); a native/sensor backend would populate `temp_c`.

### Known limitations (by design, documented)
- **Correlation vs causation:** `file_watch` precision comes from the path-reference match, *not*
  the confidence threshold — a process that merely references a recently-changed file can score a
  false edge (see `docs/adr/0001`).
- **Verification so far is macOS/arm64.** The code is OS-agnostic (gopsutil/fsnotify) but native
  backends and other OSes are untested here.

---

## Quickstart

```bash
make build          # compile the daemon (bin/cged) + set up the engine venv
make test           # full gate: schema drift + Go -race + pytest
make fixtures       # build the demo databases under test/fixtures/

# capture for a few seconds, then ask why:
./bin/cged -db /tmp/cg.db -watch "$HOME/somedir" -duration 5s
./scripts/cg tree <pid> --db /tmp/cg.db          # process + descendants
./scripts/cg path <pid> --db /tmp/cg.db          # root-ward ancestry
./scripts/cg why "why is the fan loud?" --db /tmp/cg.db
./scripts/cg why "why did pid <N> start" --db /tmp/cg.db   # shows a file cause if any

# deterministic demos (no capture needed):
./scripts/cg why "why is pid 900 using cpu" --db test/fixtures/fw.db
```

## Layout

```
shared/schema/    # event.schema.json (source of truth) + gen.py codegen
daemon/           # Go: collectors (generic poll, filewatch), pipeline, SQLite store, cged
engine/           # Python: ingest, graph (builder/scoring/traverse/edges), query (resolver/narrator/llm), cli
docs/             # architecture.md, plans/, adr/, schema.md, heuristics.md
test/fixtures/    # committed event streams (*.jsonl) for deterministic tests/goldens
```

## Picking up where we left off

All work currently lives on branch **`claude/causegraph-M3f-M4`** (it contains the full history;
`main` can fast-forward to it). The next high-leverage step is **M3-full native capture** on a
Linux box (eBPF) or Windows (ETW) — that's the domino that makes M4's remaining inferred edges
(socket, writer→reader) worth building. Each milestone was shipped behind a seam, so every
remaining item is additive, not a rewrite.
