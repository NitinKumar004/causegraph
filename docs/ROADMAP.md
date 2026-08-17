# Roadmap — what's built, what's left, what's next

> **Read this first when picking the project back up.** Everything marked ✅ is shipped **and
> tested** (`make test` is green: Go `-race` + pytest + deterministic CLI goldens). Each
> milestone has a plan in [`plans/`](plans) and design decisions in [`adr/`](adr).

## Built (shipped + tested)

| Milestone | Delivers | Plan |
|---|---|---|
| **M0** — Skeleton + contract | `event.schema.json` single source of truth → codegen Go+Python; WAL SQLite ring-buffer store behind a `Sink` seam | [plans/causegraph-M0-M2.md](plans/causegraph-M0-M2.md) |
| **M1** — Cross-platform capture | `Collector` seam + generic **gopsutil** backend (process spawn/exit + resource sampling); drop-oldest pipeline. macOS/Windows/Linux, no root | [plans/causegraph-M0-M2.md](plans/causegraph-M0-M2.md) |
| **M2** — Ancestry graph + CLI | networkx DAG from ppid (certain edges); `cg tree` / `cg path` | [plans/causegraph-M0-M2.md](plans/causegraph-M0-M2.md) |
| **M4** — resource attribution | peak CPU/RSS per process instance (pid-reuse-safe), powering the "which process is responsible" ranking the UI surfaces | [plans/causegraph-M4-M5.md](plans/causegraph-M4-M5.md) |
| **M3f + M4 core** | **fsnotify** `file.change` capture (no root) + the first **inferred (<1.0) edge** `file_watch` (a file changed → likely triggered a process); `scoring.py`; precision traversal; the UI shows the file cause | [plans/causegraph-M3f-M4.md](plans/causegraph-M3f-M4.md) + [adr/0001](adr/0001-graph-node-types-and-edge-scoring.md) |
| **M6** — web UI | read-only local viewer: `cg ui` serves a page (vendored cytoscape.js, offline) that renders the causal graph for a pid — process vs file nodes, edges coloured by rule+confidence, payload bounded (`max_nodes`); stdlib http.server API bound to 127.0.0.1 | [plans/causegraph-M6.md](plans/causegraph-M6.md) |

## Left to build

| Milestone | What it needs | Blocked by |
|---|---|---|
| **M3-full** — native backends | Real kernel capture: eBPF (Linux), ETW (Windows), EndpointSecurity (macOS) | Linux/Windows to build+test; macOS ES needs an **Apple-approved entitlement** |
| **M4 remaining rules** | `socket` (IPC), `cron`/temporal, **writer→reader** file edges | needs M3-full's process attribution |
| **Fleet** — many machines | a remote `Sink` shipping events to a central store keyed by `host_id` | none — seam exists |

### Smaller enhancements
- **`actor.cwd` capture** — so `file_watch` matches *relative*-path args (today: `exe` + absolute args only).
- **Recursive / auto file-watch** — fsnotify watches only directories present at startup.
- **Query-time graph caching** — the engine rebuilds the whole DAG per `cg` call (~2.6s / 1.6 GiB at
  1M rows). Fine for M2 scope; revisit before large windows or fleet.
- **Temperature** — "fan/heat" is inferred from sustained **CPU** (no temp sensor); a native/sensor
  backend would populate `temp_c`.

## Next phase to build

**M3-full native capture, on Linux (eBPF) or Windows (ETW).** It's the domino: real kernel capture
gives file/socket events *with the acting process attributed*, which is what makes M4's remaining
inferred edges (`socket`, writer→reader) worth building. Each milestone shipped behind a seam, so
this is additive — a new file satisfying `Collector`, nothing downstream changes.

Cheaper wins available anytime (no blockers): **`actor.cwd` capture** (so file_watch matches
relative-path args), or **Fleet** (a remote `Sink`).

When starting: run `make test` (should be green), skim [architecture.md](architecture.md) §3 and §9,
then write the milestone plan into [`plans/`](plans) before coding.

## Known limitations (by design)
- **Correlation vs causation:** `file_watch` precision comes from the path-reference match, not the
  confidence threshold — a process that merely references a recently-changed file can score a false
  edge (see [adr/0001](adr/0001-graph-node-types-and-edge-scoring.md)).
- **Verification so far is macOS/arm64.** The code is OS-agnostic (gopsutil/fsnotify) but native
  backends and other OSes are untested here.
