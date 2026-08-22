# causegraph-v1.1 — "Understand at a glance"

> Product milestone from [causegraph-product-vision.md](causegraph-product-vision.md), Tier 1.
> Goal: on opening the dashboard, a normal user *immediately* understands what their machine is
> doing — better than Activity Monitor — without reading a table of PIDs.

## Deliverables

1. **Current (live) metrics.** Surface the *latest* sample's CPU/RSS per process (`cpu_pct` /
   `rss_bytes`), not just the window peak — the "right now" value a monitor shows. *(backend done:
   `attribution.annotate` tracks `latest_*`; `/api/graph` node JSON exposes `cpu_pct`/`rss_bytes`.)*
2. **App roll-ups.** Group the process list by application (all 41 "Google Chrome Helper"
   processes → one **"Google Chrome — 62% · 41 procs"** row), sorted by current CPU. A toggle
   flips between **Apps** and **Processes** views. Clicking an app focuses its hottest process.
3. **Machine-status summary.** A one-line plain-English banner at the top:
   *"Busy — Chrome 62% (41), Python 99% (1)"* or *"Quiet — nothing notable."* Rule-based, no LLM.

## Shape

```
  left panel:
   ┌───────────────────────────────┐
   │ [ machine-status summary ] *   │   ← new: plain-English one-liner
   │ CPU · MEM · A–Z   [Apps|Procs]*│   ← new grouping toggle
   │ sliders …                      │
   │ ▸ Google Chrome   62%  41 *    │   ← app roll-up rows (default)
   │ ▸ Python          99%   1      │
   │ …                              │
   └───────────────────────────────┘
```

## Rules

- **App key** from exe: first `…/<Name>.app/…` bundle → `<Name>` (folds all Chrome helpers into
  "Google Chrome"); else the binary basename. Pure string function, unit-tested is optional
  (client-side).
- **Current-metric fallback:** `cpu_pct ?? peak_cpu_pct`, `rss_bytes ?? peak_rss_bytes` — a
  process sampled only at baseline still shows something.
- **Hot** (red) now keys off the *current* value, so the glow means "hot right now."
- **Summary:** top apps by summed current CPU; "Quiet" when the max app CPU is low.

## Acceptance criteria

- Opening the dashboard shows a readable one-line status without interaction.
- The list defaults to Apps view; Chrome's dozens of helpers appear as one row with a process
  count; toggling to Processes shows the flat per-PID list.
- CPU/MEM shown are current values; sorting matches.

## Test plan

| dimension | mechanism |
|---|---|
| correctness | backend `latest_*` = most recent sample (add a test: two samples, latest wins even if unordered) |
| reliability | current-metric fallback to peak when no live sample; empty/idle machine → "Quiet" |
| regression | existing graph/inspect untouched; `make test` green |
| ui | headless-Chrome screenshots of Apps view + summary at desktop and narrow widths |

## Open risks

- App-key heuristic won't perfectly name every daemon (fallback = basename) — acceptable; refine
  with a small alias map later. `[accepted]`
- "Busy/Quiet" thresholds are heuristic; tune from real use. `[accepted]`
