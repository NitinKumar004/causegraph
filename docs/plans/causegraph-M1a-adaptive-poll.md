# causegraph-M1a — adaptive process polling (no-permission fidelity)

> Contract. Catch short-lived processes the fixed 2s poll misses, **without any new
> permission, root, or entitlement** — so the frictionless, distributable capture floor
> gets closer to native fidelity. Behind the existing `Collector` seam; nothing
> downstream changes. Divergences from precedent tagged `[A]`.

## Goal

The generic poller does one thing every `SampleInterval` (2s): diff the process list
(spawn/exit) **and** resource-sample. A process that lives < 2s is never seen. Fix the
**diff cadence** only: poll fast during churn, back off when idle — bounded, cheap, no
privileges. Resource sampling stays slow (CPU/RSS don't need sub-second cadence).

## Shape

```
                 before                              after *
  ┌───────────────────────────┐      ┌──────────────────────────────────────┐
  │ ticker(2s)                 │      │ diffTimer(adaptive [min,max]) *        │
  │   scan(cpu+mem)            │      │   scan(identity only) → diff → spawn/exit│
  │   → diff + sample          │      │   adapt: churn→min, quiet→back off*     │
  └───────────────────────────┘      │ resTicker(SampleInterval)               │
                                      │   scan(cpu+mem) → resource.sample        │
                                      └──────────────────────────────────────┘
```

- `[A]` **Decouple the two cadences.** Diff runs on an adaptive timer in `[PollMin,PollMax]`;
  sampling keeps its own fixed `SampleInterval` ticker. Reversible: `PollMin>=PollMax`
  collapses back to today's single-cadence behaviour.
- `[A]` **Metric-free fast scan.** The diff path skips `CPUPercent()`+`MemoryInfo()` (the two
  costly per-process calls); it needs only pid/ppid/createNs/exe/args/user. Keeps fast ticks
  cheap so speeding up doesn't spike the recorder's own CPU. `diff()` is unchanged (it never
  read metrics).
- Adaptation: a diff tick that finds any spawn/exit → interval `=PollMin`; after
  `backoffQuiet` (3) consecutive empty ticks → `interval = min(interval*2, PollMax)`.

## Config

| knob | flag | default | meaning |
|---|---|---|---|
| `PollMin` | `-poll-min` | `250ms` | fastest diff cadence (during churn) |
| `PollMax` | `-poll-max` | `= SampleInterval` (2s) | slowest diff cadence (idle) |

`-interval` keeps its meaning (resource-sample cadence). Steady-state idle behaviour is
unchanged (diff backs off to `PollMax`); only *bursts* poll faster.

## Acceptance criteria

- A process that starts and exits between two 2s boundaries but lives > `PollMin` is
  captured (spawn **and** exit), where the old fixed poller missed it.
- Idle CPU cost is unchanged (diff cadence backs off to `PollMax`).
- Pid-reuse still yields exit+spawn (createNs still fetched on the fast scan).
- `PollMin>=PollMax` reproduces the pre-change single-cadence timing.

## Test plan

| dimension | mechanism |
|---|---|
| correctness | `diff()` unit tests unchanged; new test: churn drives interval to `PollMin`, quiet backs off to `PollMax` (pure adapt function, no gopsutil) |
| reliability | metric-free scan still emits spawn with exe/args/ppid (identity intact) |
| concurrency | one goroutine, two timers in one `select` — no shared state; race detector via `make test` |
| scale | fast ticks are `Pids`-cheap (no cpu/mem); back-off caps sustained cost — n/a formal bench, reasoned |
| security | no new syscalls, no privilege, no new flags that widen capture scope |
| regression | `PollMin>=PollMax` path == old behaviour; existing `poll_test.go` still green |

## Unknowns → spikes

`none` — mechanism is a timer split; the costly-call assumption (CPUPercent/MemoryInfo
dominate scan cost) is the standard gopsutil cost profile, confirmed by the existing
comment in `poll.go` that CPUPercent needs two reads.

## Open risks I'm accepting

- A process shorter-lived than `PollMin` (250ms) is still missed — that's the polling-tier
  ceiling; native capture (M3-full) is the only fix, and it needs privileges. `[unresolved]`
- Adaptive timing is best-effort, not a guarantee — a burst of many short procs inside one
  `PollMin` window can still coalesce. Acceptable at this tier.
