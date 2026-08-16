# Heuristics — edge rules and their confidence

Each causal edge is proposed by an `EdgeRule` plugin (`engine/causegraph/graph/edges/`).
The builder runs every registered rule; adding a heuristic is a new file, never a builder
change. Precision over recall: we prefer three edges we are sure of to thirty we are guessing
(architecture.md §5.3).

## Shipped (M2)

| Rule | File | Signal | Confidence |
|---|---|---|---|
| `spawn` | `edges/spawn.py` | `actor.ppid` — the parent live at the child's spawn ts | **1.0** (certain) |

The spawn rule is the only certain edge: the OS told us the parent pid. Node identity is
`(pid, spawn_ts)`, so a reused pid over the retention window is two nodes and a child never
attaches to the wrong parent.

## Sampling caveat (M1, generic backend)

`gopsutil` `CPUPercent()` returns 0/coarse on the first read of a process (it needs two reads
to compute a delta). A process that is busy but freshly seen may miss the CPU threshold on its
first tick. Documented in `daemon/internal/collector/generic/poll.go`.

## Deferred (M4)

`file_watch`, `socket`, `cron`, `resource` — inferred edges with confidence < 1.0, combined by
`scoring.py` (rule base × event provenance × temporal tightness). Not built here.
