# CauseGraph — product vision & feature roadmap

> This is the **product** roadmap (what to build for users and why). For the
> engineering/milestone roadmap (what's shipped, seams, native capture) see
> [`../ROADMAP.md`](../ROADMAP.md).

## The one insight

Activity Monitor / Task Manager / htop answer **"what is my machine doing *right now*?"** —
a live snapshot. They cannot answer the questions people actually ask:

- "Why did my fan get loud 10 minutes ago?" (it's quiet now — a snapshot is useless)
- "What drained my battery since lunch?"
- "Why did my Mac hang for 30 seconds?"
- "What keeps respawning in the background?"

CauseGraph already has the two things a task manager doesn't: **history** (it's a recorder)
and **causality** (the graph). That is the moat. So the goal is **not** "a prettier task
manager" — it's **"the thing you open *after* something happened."** A flight recorder.

## Principles (non-negotiable)

- **Local-only.** Nothing leaves the machine. No account, no cloud.
- **No LLM.** Explanations are deterministic, rule-based templates — trustworthy, never
  hallucinated. (This is a feature, not a limitation.)
- **No root / no approvals.** Everything uses the unprivileged `gopsutil`/`fsnotify` capture
  floor. Richer signals are added by sampling more, never by escalating privilege.
- **A normal user must understand it at a glance** — every screen has a one-line plain-English
  summary, not just tables of numbers.

## Tier 1 — Trust (match a monitor, so people rely on it)

Users won't trust a tool that shows less than Activity Monitor. Table stakes:

- **Per-process CPU, memory, disk I/O, network, energy** (all available from gopsutil, no root).
- **Group by app, not PID.** Activity Monitor shows 40 "Google Chrome Helper" rows; roll them
  into **"Chrome — 62% CPU across 41 processes."** Better than the built-in tool on day one.
- **Top "machine status" line** in plain English:
  *"Busy — Chrome is using 62% CPU (41 tabs); a Python script has held 99% for 3 min."*

## Tier 2 — Differentiators (nobody does these well locally)

1. **Time-travel / rewind** 🏆 — a **timeline scrubber**; drag to 2:14pm and the whole graph
   shows what the machine was doing *then*. History is already stored; this is a UI + query
   feature. No consumer tool has this.
2. **Incident feed** — auto-detect "notable moments" with one-line summaries: CPU spike, memory
   **leak** (steadily climbing RSS), **crash-loop** (a process dying and respawning), a burst of
   short-lived processes, a fan-loud window. Each a card: *"14:03 — `node` respawned 22× in 40s."*
3. **"Why is it slow right now?"** — one button walks the graph + resource attribution and gives
   a **ranked, plain-English root cause** with confidence. Deterministic, never hallucinated.
4. **"What changed?"** — diff two moments: what's new, what died, what grew.
5. **Battery / energy story** — what used the most energy since you unplugged, over time.

## Tier 3 — Novel ("nobody's done it properly")

- **Shareable incident report** — capture a 2-minute window and export a single self-contained
  HTML "black box" of what the machine did during a hang/crash. Hand it to IT or a developer.
- **Alerts / watches** — "ping me when anything holds >80% CPU for 30s" or "when this app
  respawns." A quiet recorder that speaks up only when it matters.
- **Causal replay of a freeze** — after a beachball/hang, one click shows the exact chain of
  processes and file/IO events leading into it. What pros use Instruments/dtrace for — for
  normal users, zero setup.

## What each tier needs (be realistic)

| Feature | Needs | Effort |
|---|---|---|
| App roll-ups, machine-status summary | UI + engine on data we already have | **low** |
| Time-travel scrubber | UI + a "graph as of time T" query (history already stored) | **medium** |
| Incident feed (spike/leak/crash-loop) | engine: scan the event series for patterns + templated summaries | **medium** |
| "What changed?" diff | engine: two-snapshot diff | **low–medium** |
| Disk / net / energy / threads / connections | **recorder upgrade** (`cged` samples more via gopsutil, still no root) | **medium** |
| Shareable incident export | serialize a window → self-contained HTML | **medium** |
| Alerts | a small rules engine in the daemon + a notifier | **medium** |

## Milestones

- **v1.1 — "Understand at a glance."** App roll-ups + the top plain-English machine-status
  summary + richer capture (disk/net/energy). *Immediately more useful than Activity Monitor.*
- **v1.2 — "The time machine."** Timeline scrubber (rewind) + incident feed (spikes, leaks,
  crash-loops) with one-line summaries. *The "no one did this properly" moment.*
- **v1.3 — "Black box."** Shareable incident export + alerts/watches.

## Recommended sequencing

Start with **v1.1** — it's the fastest win, needs no new capture for the roll-ups/summary, and
raises the floor so users trust the tool. Then **v1.2's timeline + incident feed** is the
differentiator that turns "a prettier task manager" into "the flight recorder for your machine"
— the original vision.

Each item ships behind the existing seams (`Collector` for capture, the read-only API + UI for
presentation), so nothing here is a rewrite — it's additive.
