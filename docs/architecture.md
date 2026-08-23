# How CauseGraph works

CauseGraph is a **flight recorder for your computer**. It quietly watches what happens —
programs starting and stopping, files changing, CPU and memory moving — and turns that history
into a **causal graph**: not just *what* ran, but *why* it ran and *what it set off*.

You open a dashboard, click a process, and see the chain: what launched it, what it launched,
and what likely triggered it. Everything runs on your own machine; nothing is uploaded.

---

## The pipeline

The system is a simple one-way flow. Each stage does one job and hands off to the next, so any
stage can be restarted or rewritten without touching the others.

```
                         your machine
                              │   programs start & stop, files change,
                              │   CPU / memory move
                              ▼
                  ┌──────────────────────────┐
                  │   RECORDER  (always-on)   │   watches the OS and turns every
                  │                           │   signal into one plain "event"
                  └────────────┬──────────────┘
                               │  events
                               ▼
                  ┌──────────────────────────┐
                  │   STORE  (on disk)        │   a capped ring buffer — oldest
                  │                           │   events fall off, so it can never
                  └────────────┬──────────────┘   fill the disk it's diagnosing
                               │  recorded history
                               ▼
                  ┌──────────────────────────┐
                  │   ENGINE                  │   reads the history and works out
                  │   builds the causal graph │   who caused what
                  └────────────┬──────────────┘
                               │  graph
                               ▼
                  ┌──────────────────────────┐
                  │   DASHBOARD  (browser)    │   pick a process → see its causes
                  │   local, read-only        │   and effects as a picture
                  └──────────────────────────┘
```

- **Recorder** — an always-on background program. Its *only* job is to translate whatever the
  operating system reports into one common event shape, so nothing above it has to care whether
  it's running on macOS, Linux, or Windows.
- **Store** — a small local database. It keeps a rolling window of recent history and
  automatically drops the oldest events, so it stays bounded and never grows without limit.
- **Engine** — reads the recorded events and assembles them into a graph in memory: nodes are
  processes and files; edges are causal links between them.
- **Dashboard** — a page served only to your own machine. It draws the graph and lets you walk
  it. It shows what the engine already worked out — it does no thinking of its own.

---

## How it decides what caused what

Raw events are just a flat timeline. **The operating system tells you what happened, but not
why** — CauseGraph infers the "why" and marks how sure it is with a **confidence** score.

- **Certain links (confidence 1.0).** When a program starts, the OS tells you its parent. That
  parent → child link is a fact, not a guess. These alone give you the full family tree of
  everything running.
- **Likely links (confidence below 1.0).** A file changed a moment before a program that reads
  it started → that change probably triggered it. These are educated inferences, each scored by
  how strong the signal is.

The guiding rule is **precision over recall**: better to show a few links you're sure of than a
tangle of maybes. High-confidence links are always shown; weaker guesses stay out of the way
until you ask for them. This is the hard part of the whole system — *near in time is not the
same as caused* — so the bias is always toward being sure.

---

## Why it's built this way

- **Normalize early, stay OS-agnostic forever after.** Every OS reports activity differently.
  The recorder absorbs all of that and emits one canonical event; the rest of the system speaks
  only that one language. This single decision is what makes CauseGraph portable and simple to
  extend — teaching it a new source of data, or a new way to infer a cause, is an addition, not
  a rewrite.
- **Local-first and bounded.** Everything stays on your machine. The store is capped, resource
  sampling is coarse, and the recorder is designed to stay out of the way — a tool that watches
  your machine must never be the thing slowing it down.
- **Right tool per job.** The recorder is optimized to run efficiently in the background; the
  engine is optimized for fast iteration on the causal logic — the part that improves most over
  time. They meet at one clean contract (the event and the store), so each side evolves on its
  own.

For the exact shape of an event — the contract both sides agree on — see
[schema.md](schema.md).
