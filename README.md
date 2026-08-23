# CauseGraph

**A flight recorder for your computer.** It quietly records what your machine does — programs
starting and stopping, files changing, CPU and memory moving — and shows you *why*: click any
program and see what launched it, what it launched, and what likely set it off.

Everything runs on your own machine. No account, no root, nothing is uploaded.

## Install

One line (needs only **Python 3.10+** — no compiler, no build):

```bash
curl -fsSL https://raw.githubusercontent.com/NitinKumar004/causegraph/main/install.sh | sh
```

It verifies the download, installs, and starts itself — a background recorder plus the
dashboard, now and on every login. When it finishes, open **http://localhost:8765**.

Prefer Homebrew?

```bash
brew install NitinKumar004/causegraph/causegraph
cg up
```

## What you'll see

At **http://localhost:8765**: the programs running on your machine on the left (busiest first),
their causal graph in the middle, details on the right. It refreshes on its own every few
seconds. Click any program to trace where it came from and what it started — the busiest one is
picked for you.

## Everyday commands

```bash
cg status     # is it recording? how fresh is the data?
cg down       # stop the background recorder
cg up         # start it again and open the dashboard
cg teardown   # stop it and remove the auto-start
cg tree <pid> # print a process and its descendants as text
```

Recorded history and logs live in `~/.causegraph/` and never leave your machine.

## Build from source (developers)

Needs **Go 1.26+** and **Python 3.10+**.

```bash
git clone https://github.com/NitinKumar004/causegraph.git
cd causegraph
make build     # builds the recorder and sets up an isolated Python environment
make test      # optional: run the full test suite
```

`make build` keeps everything inside the project (`engine/.venv`), so it never touches your
system Python.

## Where things stand

Verified on **macOS (Apple Silicon)**. The code is OS-agnostic, so Linux and Windows should
work too but aren't battle-tested yet — try it and open an issue if something's off. Capture
uses a portable polling backend today (starts, stops, CPU/memory, and changes in watched
folders); deeper native capture is planned.

## Documentation

- **[docs/architecture.md](docs/architecture.md)** — how it works, with a diagram.
- **[docs/schema.md](docs/schema.md)** — the event format the recorder and engine share.
