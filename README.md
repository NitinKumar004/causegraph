# CauseGraph

*A flight recorder for your computer. It traces why a process is running — CPU, memory, and
the file that likely triggered it — back to a root you can act on.*

Two processes, one contract: a **Go daemon** (`cged`) captures OS activity into one canonical
event schema and writes it to SQLite; a **Python engine** (`cg`) builds an in-memory causal
graph from those events and lets you navigate it — pick a process (or the hottest one) and read
its causal neighborhood. **No LLM: the graph is the answer**, and the reasoning stays in
deterministic traversal you can fully test.

Everything runs locally — **no root/admin, nothing leaves your machine.**

## Install (one line, then it just runs)

Needs only **Python 3.10+** — no Go, no compiler, no build.

```bash
curl -fsSL https://raw.githubusercontent.com/NitinKumar004/causegraph/main/install.sh | sh
```

The installer downloads the prebuilt bundle for your OS/arch and **sets it up to run
automatically** (`cg setup`): a background recorder and the dashboard both start now and on every
login. When it finishes, just open **http://localhost:8765** — that's it, nothing to run.

- **Stop / remove it:** `cg teardown`
- **Check it:** `cg status`

Managing it by hand instead? See [manual control](#manual-control) and
[build from source](#or-build-from-source-developers).

## Or build from source (developers)

- **Go 1.26+** ([install](https://go.dev/dl/)) + **Python 3.10+**.

```bash
git clone https://github.com/NitinKumar004/causegraph.git
cd causegraph
make build     # compiles the daemon to bin/cged and sets up the Python engine
```

`make build` creates a self-contained Python environment under `engine/.venv`, so it
won't touch your system Python. (Optional: `make test` runs the full suite to confirm all is well.)

## What you'll see

Open **http://localhost:8765**: every process on the left (sorted by CPU), the causal graph in the
middle, details on the right. It **refreshes itself every few seconds** — the header shows `live`
while recording (and `frozen` if the recorder ever stops). Click any process to trace where it came
from and what it spawned; the hottest one is picked for you. Everything stays on your machine.

## Manual control

`cg setup` (above) is the hands-off way. If you'd rather run it yourself:

```bash
cg up        # one-off: background recorder + serve the UI (Ctrl-C stops the UI, not the recorder)
cg down      # stop the background recorder
cg status    # recording? how fresh is the data?
cg service install / uninstall   # just the recorder as a login service (no UI service)
cg tree <pid> / cg path <pid>    # print the tree as text
```

State (the rolling database, logs) lives in `~/.causegraph/`.

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
