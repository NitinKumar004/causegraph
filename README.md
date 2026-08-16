# CauseGraph

*A flight recorder for your computer. Ask it why anything is happening; it traces the cause
back to a root you can act on.*

Two processes, one contract: a **Go daemon** (`cged`) captures OS activity into one canonical
event schema and writes it to SQLite; a **Python engine** (`cg`) builds an in-memory causal
graph from those events and answers questions like *"why is the fan loud?"* or *"why did this
process start?"* — with the reasoning kept in the graph, not in a model.

## Quickstart

```bash
make build          # compile the daemon (bin/cged) + set up the engine venv
make test           # full gate: schema drift + Go -race + pytest
make fixtures       # build the demo databases under test/fixtures/

# capture for a few seconds, then ask why:
./bin/cged -db /tmp/cg.db -watch "$HOME/somedir" -duration 5s
./scripts/cg why "why is the fan loud?" --db /tmp/cg.db
./scripts/cg tree <pid> --db /tmp/cg.db     # a process and its descendants
```

## Documentation

All documentation lives in **[`docs/`](docs/)** — start there:

- **[docs/ROADMAP.md](docs/ROADMAP.md)** — what's built, what's left, and the next phase to build.
- **[docs/architecture.md](docs/architecture.md)** — the full design.
- **[docs/](docs/)** — plans, ADRs, schema and heuristics reference.
