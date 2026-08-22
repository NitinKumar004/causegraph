# CauseGraph

*A flight recorder for your computer. It traces why a process is running — CPU, memory, and
the file that likely triggered it — back to a root you can act on.*

Two processes, one contract: a **Go daemon** (`cged`) captures OS activity into one canonical
event schema and writes it to SQLite; a **Python engine** (`cg`) builds an in-memory causal
graph from those events and lets you navigate it — pick a process (or the hottest one) and read
its causal neighborhood. **No LLM: the graph is the answer**, and the reasoning stays in
deterministic traversal you can fully test.

Everything runs locally — **no root/admin, nothing leaves your machine.**

## Install — download & run (no build)

The easy path. Needs only **Python 3** (preinstalled on macOS/Linux) — no Go, no compiler.

```bash
curl -fsSL https://raw.githubusercontent.com/NitinKumar004/causegraph/main/install.sh | sh
cg up          # starts recording in the background + opens the live UI
```

The installer grabs the prebuilt release for your OS/arch (a self-contained bundle: the recorder
binary + engine + a vendored copy of its one dependency). Jump to [Using it](#using-it--one-command).

## Or build from source (developers)

- **Go 1.26+** ([install](https://go.dev/dl/)) + **Python 3.10+**.

```bash
git clone https://github.com/NitinKumar004/causegraph.git
cd causegraph
make build     # compiles the daemon to bin/cged and sets up the Python engine
```

`make build` creates a self-contained Python environment under `engine/.venv`, so it
won't touch your system Python. (Optional: `make test` runs the full suite to confirm all is well.)

## Using it — one command

```bash
./scripts/cg up          # starts a background recorder + opens the live UI
```

That's the whole thing. `cg up`:
- starts a **background recorder** that keeps capturing even after you close the terminal
  (state lives in `~/.causegraph/`),
- serves the viewer and opens **http://127.0.0.1:8765** in your browser.

You'll see every process on the left (sorted by CPU), the causal graph in the middle, and details
on the right. It **refreshes itself every few seconds** — the header shows `live` while the
recorder is running. Click any process to trace where it came from and what it spawned; the
hottest one is picked for you.

```bash
./scripts/cg status      # is it recording? how fresh is the data?
./scripts/cg down        # stop the background recorder
```

`Ctrl-C` closes the viewer but leaves the recorder running (that's the point — it's a flight
recorder). Use `cg down` to actually stop capturing.

Prefer the terminal? `./scripts/cg tree <pid>` and `./scripts/cg path <pid>` print the tree as
text (add `--db ~/.causegraph/live.db`).

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
