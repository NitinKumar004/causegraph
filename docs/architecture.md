# CauseGraph — Architecture & Build Plan

*A flight recorder for your computer. Ask it why anything is happening; it traces the cause back to a root you can act on.*

This document is the end-to-end plan: design principles, the cross-platform strategy, the full file structure, every layer in depth, the extensibility seams, the scalability path, and a build order that gets you from nothing to a working demo without stacking all the hard things at once.

---

## 1. The one design principle everything hangs on

**Normalize early, stay OS-agnostic forever after.**

Every operating system hands you *different* raw signals (eBPF on Linux, EndpointSecurity on macOS, ETW on Windows). If any of that OS-specific shape leaks past the capture layer, your graph code, your heuristics, and your query layer all become three-headed and unmaintainable.

So the capture layer's only job is to translate whatever the OS gives it into **one canonical `Event` schema**. Everything above that line — storage, graph building, edge inference, natural-language query — sees only canonical events and never knows or cares which OS produced them. This single decision is what makes the whole system portable, extensible, and testable.

The corollary: **the canonical event schema is the most important artifact in the repo.** Design it before you write a collector.

---

## 2. System at a glance

```
┌───────────────────────────────────────────────────────────────┐
│                     DAEMON  (Go, per-machine)                    │
│                                                                  │
│  OS Collector (swappable backend)                                │
│    ├─ linux   → eBPF / fanotify                                   │
│    ├─ darwin  → EndpointSecurity / FSEvents                       │
│    ├─ windows → ETW                                               │
│    └─ generic → gopsutil polling  (works on ALL three)           │
│         │                                                         │
│         ▼  emits canonical Event                                 │
│    Pipeline (buffer • batch • backpressure • drop-oldest)        │
│         │                                                         │
│         ▼                                                         │
│    Store sink (swappable)                                         │
│         └─ local: SQLite ring buffer                             │
│         └─ fleet: remote sink (later)                            │
└───────────────────────────────────────────────────────────────┘
              │  clean seam: events at rest
              ▼
┌───────────────────────────────────────────────────────────────┐
│                     ENGINE  (Python)                             │
│    Ingest (read events)                                          │
│         ▼                                                         │
│    Graph builder → in-memory DAG (networkx)                      │
│         ▲                                                         │
│    Edge-inference rules (plugins, each scored by confidence)     │
│         ▼                                                         │
│    Query:  resolver → backward traversal → narrator → LLM        │
└───────────────────────────────────────────────────────────────┘
              │
              ▼
        Interface:  CLI  →  local web UI (d3 / cytoscape)
```

Two processes, two languages, one contract between them (the event schema + the store). Either side can be restarted, rewritten, or debugged without touching the other.

---

## 3. The cross-platform strategy (the core of your ask)

Three ideas make "runs on macOS, Windows, and Linux" real instead of aspirational.

### 3.1 A single collector interface, many backends

In Go, the collector is one interface. Each OS is a separate file selected at compile time by build tags, so a build for one OS never even compiles the others' code.

```go
// collector/collector.go  — shared, no OS specifics
type Collector interface {
    // Start streams canonical events into the channel until ctx is cancelled.
    Start(ctx context.Context, out chan<- event.Event) error
    // Capabilities lets the engine know what fidelity to expect.
    Capabilities() Caps
    Close() error
}

type Caps struct {
    ProcessSpawn  bool
    ProcessExit   bool
    FileEvents    bool
    SocketEvents  bool
    ResourceStats bool
    Fidelity      Fidelity // Polling | Native
}
```

```go
// collector/collector_linux.go
//go:build linux
func New(cfg Config) (Collector, error) { return linux.NewEBPF(cfg) }

// collector/collector_darwin.go
//go:build darwin
func New(cfg Config) (Collector, error) { return darwin.NewES(cfg) }

// collector/collector_windows.go
//go:build windows
func New(cfg Config) (Collector, error) { return windows.NewETW(cfg) }
```

Adding a new platform, or a new capture technology on an existing one, means writing one file that satisfies `Collector`. Nothing downstream changes. That is the extensibility seam for capture.

### 3.2 A generic fallback that works everywhere on day one

Before you touch a single kernel API, ship a `generic` backend built on **`gopsutil`** that polls the process list and diffs snapshots to infer spawns/exits, plus per-process CPU/mem. It is lower fidelity (you miss short-lived processes between polls, you don't get real file events) — but it compiles and runs on all three OSes immediately with zero entitlements, zero root, zero approval.

This gives you a real end-to-end system in week one, and a permanent floor: any OS you haven't written a native backend for still *works*, just at reduced fidelity. Native backends are upgrades, not prerequisites.

### 3.3 The canonical event schema everyone agrees on

One language-agnostic schema in `shared/schema/event.schema.json`, from which you generate Go structs and Python dataclasses so the two sides can never drift.

```jsonc
{
  "id":         "uuid",              // unique event id
  "ts":         "int64",            // nanoseconds, monotonic where possible
  "host_id":    "string",           // enables fleet mode later
  "kind":       "enum",             // process.spawn | process.exit | file.change | net.connect | resource.sample | ...
  "actor": {                          // the process this event is about
    "pid":  "int64",
    "ppid": "int64",                // parent pid — your first CERTAIN causal edge
    "exe":  "string",
    "args": "string[]",
    "user": "string"
  },
  "target": {                         // optional: file path, socket 5-tuple, etc.
    "path":   "string?",
    "socket": "string?"
  },
  "metrics": {                        // optional: for resource.sample events
    "cpu_pct":  "float?",
    "rss_bytes":"int64?",
    "temp_c":   "float?"
  },
  "source":     "enum",             // ebpf | es | etw | poll — provenance, useful for scoring
  "confidence": "float"             // 1.0 for observed facts; <1 reserved for inferred
}
```

Downstream code branches on `kind`, never on OS. `source` is carried through so the edge-inference layer can trust a natively-observed spawn more than a polled one.

---

## 4. Full file structure

```
causegraph/
├── README.md
├── shared/
│   └── schema/
│       ├── event.schema.json          # SINGLE source of truth for the event format
│       └── gen/                        # codegen’d Go structs + Python dataclasses
│
├── daemon/                             # Go capture daemon
│   ├── go.mod
│   ├── cmd/
│   │   └── cged/
│   │       └── main.go                 # entrypoint: wire config → collector → pipeline → store
│   └── internal/
│       ├── collector/
│       │   ├── collector.go            # Collector interface + Caps (shared)
│       │   ├── collector_linux.go      # //go:build linux
│       │   ├── collector_darwin.go     # //go:build darwin
│       │   ├── collector_windows.go    # //go:build windows
│       │   ├── generic/                # gopsutil polling — compiles on ALL OSes
│       │   │   └── poll.go
│       │   ├── linux/                  # eBPF programs + loader (cilium/ebpf)
│       │   ├── darwin/                 # EndpointSecurity client (cgo bridge) + FSEvents
│       │   └── windows/                # ETW consumer
│       ├── event/
│       │   ├── event.go                # canonical struct (generated) + validation
│       │   └── codec.go                # encode/decode
│       ├── pipeline/
│       │   ├── buffer.go               # ring buffer, drop-oldest under pressure
│       │   ├── batch.go                # batched writes to the store
│       │   └── backpressure.go         # collector never blocks on the store
│       ├── store/
│       │   ├── store.go                # Sink interface (swappable)
│       │   ├── sqlite.go               # local ring-buffer store (default)
│       │   └── remote.go               # fleet sink (later)
│       └── config/
│           └── config.go
│
├── engine/                             # Python graph + query
│   ├── pyproject.toml
│   └── causegraph/
│       ├── ingest/
│       │   └── reader.py               # stream events out of SQLite
│       ├── graph/
│       │   ├── builder.py              # assemble the in-memory DAG (networkx)
│       │   ├── model.py                # Node / Edge types
│       │   ├── scoring.py              # confidence combination + edge ranking
│       │   ├── traverse.py             # backward walk to root causes
│       │   └── edges/                  # ← the intellectual heart, plugin-per-rule
│       │       ├── base.py             # EdgeRule interface
│       │       ├── spawn.py            # ppid → certain edge (confidence 1.0)
│       │       ├── file_watch.py       # file.change shortly before a spawn that holds it
│       │       ├── socket.py           # matching socket write/read → IPC edge
│       │       ├── cron.py             # scheduled job → known child process
│       │       └── resource.py         # attribute temp/fan spikes to a process
│       ├── query/
│       │   ├── resolver.py             # English question → entry node in the graph
│       │   ├── narrator.py             # causal path → plain-English explanation
│       │   └── llm.py                  # LLM adapter — swappable provider, TRANSLATOR only
│       └── api/
│           └── server.py               # local HTTP server for the web UI
│
├── ui/                                 # web UI (later milestone)
│   └── ...                             # static page; d3 / cytoscape causal tree
│
├── deploy/
│   ├── macos/                          # system-extension packaging, notarization, entitlements
│   ├── windows/                        # service install, ETW manifest
│   └── linux/                          # systemd unit, capabilities
│
├── docs/
│   ├── architecture.md                 # this file
│   ├── schema.md
│   └── heuristics.md                   # each edge rule + how its confidence is derived
└── test/
    └── fixtures/                       # recorded event streams for deterministic replay tests
```

---

## 5. Layer by layer

### 5.1 Capture daemon (Go)

**Non-negotiable: the recorder must be nearly invisible.** If your "why is my machine slow" tool is what's slowing the machine, you've failed. Three rules enforce this:

1. **The collector never blocks on the store.** It emits into a bounded channel; the pipeline drains it. If the store falls behind, the pipeline drops *oldest* events (never newest) and increments a `dropped` counter — you'd rather lose old history than stall live capture.
2. **Batched, WAL-mode SQLite writes.** Never one INSERT per event. Accumulate and write in transactions on a timer or size threshold.
3. **Sampling, not firehose, for resource stats.** Process spawn/exit are events; CPU/mem/temp are *sampled* at a coarse interval (e.g. 1–2s) and only for processes that matter.

Each native backend lives fully inside its OS folder and touches nothing shared except the `Event` type it emits:
- **Linux** — `cilium/ebpf` for process + file hooks; `fanotify` as an alternative for file events. Needs `CAP_BPF`/root and a recent kernel.
- **Windows** — ETW, specifically the kernel process/file providers. Needs admin, but **no approval process** — the most frictionless native backend.
- **macOS** — EndpointSecurity (via a small cgo bridge to the C API) + FSEvents. Highest fidelity, **highest friction** — see §8.

### 5.2 Store (SQLite ring buffer)

Resist the urge to reach for a time-series DB or a graph DB. For one machine, SQLite is the correct boring answer: embedded (no server → satisfies local-first), fast enough, trivial to query. Cap the events table and delete oldest so you can never fill the disk you're trying to diagnose. The `Sink` interface means "local SQLite" is just the default implementation — swapping in a remote sink for fleet mode later is a one-file change, not a rewrite.

### 5.3 Graph builder + edge inference (Python) — the heart

Raw events are a flat timeline. **Causality is a graph, and the OS does not hand you the edges** — you infer them. This is the part you could write a paper about, and it's where the product lives or dies.

Edges come in two flavors:

- **Certain edges (confidence 1.0).** Process A spawned process B — you have the parent PID. Start here; it alone gives you the process-ancestry tree that's already useful.
- **Inferred edges (confidence < 1.0).** A file changed 50ms before a process that holds it started → probably caused it. A socket write matched a socket read → IPC edge. A cron fired, then its known child appeared → temporal + known-relationship edge.

Every edge rule is a **plugin** implementing one interface:

```python
class EdgeRule(Protocol):
    def propose(self, window: EventWindow) -> list[ProposedEdge]:
        """Look at a time window of events, propose causal edges with confidence."""
```

Adding a new causal heuristic = adding one file in `graph/edges/`. This is the extensibility seam that matters most, because your understanding of causation *will* deepen over months, and you want each new insight to be an additive plugin, never a refactor.

`scoring.py` combines signals: base confidence of the rule × provenance of the underlying events (a natively-observed spawn outranks a polled one) × temporal tightness. The graph is built in memory with `networkx` at query time from the SQLite events — **do not** stand up Neo4j; your working set is "last few hours on one machine," and an in-memory DAG is faster and zero-ops. You'll know if you ever outgrow it.

> **The single hardest problem, name it now:** correlation vs causation. Two things near in time is not cause. The naive version drowns the user in false links, exactly the noise trap that kills tools like this. Build for *precision over recall*: better to show three edges you're sure of than thirty you're guessing at. Every edge carries its confidence; the traversal prefers high-confidence paths; low-confidence edges are shown only on request.

### 5.4 Query layer (Python + LLM)

Three steps, and the boundary between them and the LLM is sacred:

1. **Resolver** — map the English question to a starting node. "fan loud" → the most recent high-temperature / fan resource event.
2. **Traversal** — walk causal edges *backward* from that node toward roots, following highest-confidence paths. **This is where the reasoning happens, and it's pure graph traversal you fully control** — deterministic, testable, no model involved.
3. **Narrator** — collapse the resulting path into plain English.

The **LLM is a translator, never the reasoner.** It turns English into a graph entry point, and a structured causal path into a readable sentence. It does not decide what caused what — your graph does. That separation is the entire reason the answers are trustworthy instead of hallucinated. `llm.py` is an adapter so the provider is swappable and the rest of the system doesn't depend on any one API.

### 5.5 Interface

Start with a **CLI** — `cg why "fan is loud"` printing a causal tree is a completely legitimate v1 and skips a mountain of UI work. When you want visuals, the daemon serves a small local page and you render the causal tree with `d3` or `cytoscape.js`. A native menu-bar app is a *polish* step, not a starting point.

---

## 6. Extensibility seams, in one list

Everything you'll want to extend is already an interface, so growth is additive:

| Want to add… | You write… | Nothing else changes because… |
|---|---|---|
| A new OS, or new capture tech on an OS | one file satisfying `Collector` | downstream sees only canonical events |
| A new causal heuristic | one `EdgeRule` plugin | the builder just runs every registered rule |
| A different LLM provider | one `llm.py` adapter | query layer depends on the adapter, not the API |
| Fleet / central storage | one `Sink` implementation | the daemon already writes through the Sink interface |
| A new event type | a `kind` in the schema + rules that use it | schema is the single source of truth |

---

## 7. Scalability path

"Scalable" here has three distinct meanings; handle them in order, not all at once:

1. **High event volume on one machine** — solved in the pipeline: bounded ring buffer, batched writes, drop-oldest backpressure, coarse resource sampling. This is the only one that matters for a long time.
2. **Retention** — time-windowed ring buffer with optional tiering (keep everything for the last hour, then downsample). Configurable, bounded on disk.
3. **Fleet mode (many machines)** — the daemon→store boundary is *already* a clean seam. Swap the SQLite sink for a remote sink that ships events to a central store keyed by `host_id` (already in the schema). The engine then builds per-host or cross-host graphs. This is a real product direction, but it's a later swap, not a foundational requirement — which is exactly why the seam exists now.

---

## 8. Per-OS reality check (the honest part)

Don't get surprised by the friction; each backend has different gates:

- **Linux (eBPF)** — needs root or `CAP_BPF` and a reasonably recent kernel. Library situation is good (`cilium/ebpf`, pure-Go loader). The most pleasant native backend to develop against.
- **Windows (ETW)** — needs admin to consume kernel providers, but there is **no approval process**. Very approachable; often the easiest second backend after the generic fallback.
- **macOS (EndpointSecurity)** — the real hurdle. It requires the Apple Developer Program, the `com.apple.developer.endpoint-security.client` entitlement **which Apple must approve on request**, packaging as a system extension, and notarization. Budget real calendar time for the entitlement approval. Until then, the generic `gopsutil` backend gives you working (lower-fidelity) capture on macOS with none of this.

This is precisely why the generic fallback isn't a toy — it's what keeps every OS functional while you clear each platform's gate at its own pace.

---

## 9. Build order (stack the hard things one at a time)

The whole point of the architecture above is that value arrives at *every* milestone and no milestone requires the next one to exist.

- **M0 — Skeleton + contract.** Repo, `event.schema.json`, codegen to Go/Python, SQLite schema, a daemon that writes a heartbeat event and a Python script that reads it. *Proves the seam.*
- **M1 — Cross-platform baseline capture.** The `generic` gopsutil backend: process spawn/exit by snapshot diff + resource sampling, into SQLite. **Runs on macOS, Windows, and Linux immediately.** *You now have real data everywhere.*
- **M2 — Process-ancestry tree.** Python builds the certain-edge graph (ppid only) and a CLI prints an ancestry tree for any process. *First real end-to-end; already useful weekly.*
- **M3 — One native backend.** Pick your daily-driver OS. Real event stream + file events at native fidelity behind the same `Collector` interface. *Nothing downstream changes.*
- **M4 — Inferred edges + scoring.** The `file_watch`, `socket`, and `resource` rules; confidence scoring; precision-first traversal. *The intellectual core lands.*
- **M5 — Natural-language query.** Resolver → backward traversal → narrator → LLM adapter. *The "why is my fan loud" moment works.*
- **M6 — Web UI.** Local server + d3/cytoscape causal tree, with confidence shown on edges.
- **M7 — Port native backends** to the remaining OSes. Each is now an isolated task behind the interface, done at each platform's own pace (macOS last, given its approval gate).
- **Later — Fleet mode** via a remote `Sink`.

**The trap to avoid, restated:** do not start with native capture *and* eBPF *and* a graph database *and* a GUI all at once. That's four hard things stacked, and you'll stall before the idea proves itself. The generic backend + SQLite + in-memory networkx + CLI gets you a working, cross-platform, genuinely-useful tool first — then every hard thing after that is an isolated upgrade behind a seam you already built.

---

## 10. Tech choices, one view

| Concern | Choice | Why |
|---|---|---|
| Capture daemon | **Go** | Fast, great for long-running daemons, `cilium/ebpf` + ETW libs + `gopsutil` all first-class |
| Cross-platform capture | **Collector interface + build-tag backends + generic gopsutil fallback** | Portable day one; native backends are additive upgrades |
| Canonical schema | **JSON Schema → generated Go/Python** | Single source of truth; the two languages can't drift |
| Event store | **SQLite, ring buffer, WAL, batched writes** | Embedded, local-first, can't fill the disk |
| Store abstraction | **`Sink` interface** | Local now, remote/fleet later with one implementation |
| Graph + heuristics | **Python + networkx, in-memory at query time** | Fast iteration on the part you'll rewrite most; zero-ops |
| Edge inference | **Plugin per `EdgeRule`** | New causal insight = new file, never a refactor |
| NL query | **LLM adapter, translator-only** | Graph reasons; model only phrases → trustworthy answers |
| Interface | **CLI first → local web (d3/cytoscape)** | Skip GUI work until the logic is proven |

---

*Build M0 through M2 first. If the process-ancestry CLI already makes you reach for it during a normal workday, you'll know the idea is real — and everything after that is an upgrade behind a seam you've already designed for.*
