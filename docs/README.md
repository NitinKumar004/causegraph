# Documentation

Everything about CauseGraph lives here.

- **[ROADMAP.md](ROADMAP.md)** — what's built, what's left, and the next phase to build. **Start here.**
- **[architecture.md](architecture.md)** — the full end-to-end design (principles, layers, seams, build order).
- **[schema.md](schema.md)** — the canonical event schema and how codegen keeps Go/Python in sync.
- **[heuristics.md](heuristics.md)** — each edge rule and how its confidence is derived.
- **[plans/](plans)** — the per-milestone build plans (the contract each milestone was built against).
- **[adr/](adr)** — architecture decision records (e.g. [0001](adr/0001-graph-node-types-and-edge-scoring.md): graph node types + edge scoring).
- **[conventions.md](conventions.md)** — conventions learned while building; follow these.
- **[memory/](memory)** — a grep-checkable map of the codebase (subsystem → paths).

## Building the next phase
See **[ROADMAP.md → Next phase to build](ROADMAP.md#next-phase-to-build)**, then write the new
milestone's plan into [plans/](plans) before writing code.
