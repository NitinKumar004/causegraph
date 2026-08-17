# CauseGraph

*A flight recorder for your computer. It traces why a process is running — CPU, memory, and
the file that likely triggered it — back to a root you can act on.*

Two processes, one contract: a **Go daemon** (`cged`) captures OS activity into one canonical
event schema and writes it to SQLite; a **Python engine** (`cg`) builds an in-memory causal
graph from those events and lets you navigate it — pick a process (or the hottest one) and read
its causal neighborhood. **No LLM: the graph is the answer**, and the reasoning stays in
deterministic traversal you can fully test.

## What you need first

- **Go 1.26+** — to build the capture daemon. ([install](https://go.dev/dl/))
- **Python 3.10+** — runs the graph engine and the web viewer. (macOS/Linux ship with it.)
- **macOS, Linux, or Windows.** Capture uses `gopsutil`, so **no root/admin needed**. Everything
  runs locally — nothing is ever sent off your machine.

> Heads-up: this is a developer tool you build from source, not a click-to-install app. The steps
> below take about a minute.

## Setup (one time)

```bash
git clone https://github.com/NitinKumar004/causegraph.git
cd causegraph
make build     # compiles the daemon to bin/cged and sets up the Python engine
```

That's it. `make build` creates a self-contained Python environment under `engine/.venv`, so it
won't touch your system Python. (Optional: `make test` runs the full suite to confirm all is well.)

## Using it

**1. Record what your machine is doing** for a few seconds. This writes a capture to a database
file — pick any path you like:

```bash
./bin/cged -db /tmp/cg.db -sample-min-cpu 0 -sample-min-rss 0 -duration 8s
```

*(Add `-watch "$HOME/some/dir"` to also record file changes in a folder — that's what lets it
link "this file changed → that process woke up".)*

**2. Open the viewer:**

```bash
./scripts/cg ui --db /tmp/cg.db
```

Now visit **http://127.0.0.1:8765** in your browser. You'll see every process on the left
(sorted by CPU), the causal graph in the middle, and details on the right. Click any process to
trace where it came from and what it spawned; the hottest one is selected for you. Prefer the
terminal? `./scripts/cg tree <pid> --db /tmp/cg.db` prints the same tree as text.

## Where things stand

Works today and verified on **macOS (Apple Silicon)**. The code is OS-agnostic, so Linux and
Windows should work too, but they haven't been battle-tested yet — try it and file an issue if
something's off. Capture currently uses a portable polling backend (spawns, exits, CPU/RSS,
watched-folder file changes); deeper native kernel capture is on the roadmap.

## Documentation

All documentation lives in **[`docs/`](docs/)** — start there:

- **[docs/ROADMAP.md](docs/ROADMAP.md)** — what's built, what's left, and the next phase to build.
- **[docs/architecture.md](docs/architecture.md)** — the full design.
- **[docs/](docs/)** — plans, ADRs, schema and heuristics reference.
