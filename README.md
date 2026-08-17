# CauseGraph

*A flight recorder for your computer. It traces why a process is running — CPU, memory, and
the file that likely triggered it — back to a root you can act on.*

Two processes, one contract: a **Go daemon** (`cged`) captures OS activity into one canonical
event schema and writes it to SQLite; a **Python engine** (`cg`) builds an in-memory causal
graph from those events and lets you navigate it — pick a process (or the hottest one) and read
its causal neighborhood. **No LLM: the graph is the answer**, and the reasoning stays in
deterministic traversal you can fully test.

## Quickstart

```bash
make build          # compile the daemon (bin/cged) + set up the engine venv
make test           # full gate: schema drift + Go -race + pytest
make fixtures       # build the demo databases under test/fixtures/

# capture for a few seconds, then explore the causal graph:
./bin/cged -db /tmp/cg.db -watch "$HOME/somedir" -duration 5s
./scripts/cg tree <pid> --db /tmp/cg.db     # a process and its descendants
./scripts/cg path <pid> --db /tmp/cg.db     # root-ward ancestry of a process
./scripts/cg ui  --db /tmp/cg.db            # local web viewer at http://127.0.0.1:8765
```

## Documentation

All documentation lives in **[`docs/`](docs/)** — start there:

- **[docs/ROADMAP.md](docs/ROADMAP.md)** — what's built, what's left, and the next phase to build.
- **[docs/architecture.md](docs/architecture.md)** — the full design.
- **[docs/](docs/)** — plans, ADRs, schema and heuristics reference.
