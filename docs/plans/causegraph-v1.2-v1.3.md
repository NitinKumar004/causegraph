# causegraph-v1.2 + v1.3 — Time machine & Black box

> Product milestones from [causegraph-product-vision.md](causegraph-product-vision.md), Tiers 2–3.
> Ships in ONE PR (branch `feat/v1.1-glance`). Everything stays within the existing seams:
> Go daemon → SQLite → read-only Python API (`server.py`) → vanilla-JS + cytoscape UI.
> **Principles (unchanged): local-only, no LLM, no root, deterministic, always a plain-English
> summary, and it must FEEL fast.**

## Architecture fit (how each piece slots in)

- **No new capture.** Incidents, timeline, and the report are all *analyses of events we already
  record*. Alerts evaluate client-side. So no daemon/schema change, no root.
- **Reuse the incremental cache.** `_load(db)` already keeps parsed events in memory and appends
  only new rows (~0.8s to rebuild from memory, instant when nothing changed). Every new analysis is
  a single pass over that in-memory list — cheap, and it rides the same cache.
- **Read-only, deterministic.** New endpoints only read. Plain-English strings are rule-based
  templates (no model), so they never hallucinate.
- **Latency is a feature.** New work is lazy (fetched on demand / with the poll, never blocking the
  first paint), debounced (timeline scrub), and bounded (top-N). Target: no interaction >1 request,
  each ≤ ~0.8s on a 1M-event capture; scrubbing debounced so it feels responsive.

---

## v1.2a — Incident feed  *(auto-detected notable moments)*

**Goal.** Answer "what's worth my attention?" without reading tables. One pass over the events +
graph flags incidents, each a plain-English card.

**Detectors (deterministic, tuned to avoid false positives):**
| kind | rule | title example |
|---|---|---|
| `cpu` | a process's peak CPU ≥ 90% (with sample count) | "cged hit 106% CPU" |
| `leak` | RSS grew ≥1.6× **and** ≥64 MiB over ≥8 samples | "node memory grew 120 MiB → 512 MiB" |
| `crashloop` | ≥5 **short-lived** (<5s, observed-spawn) instances of one exe | "sleep respawned 24×" |

Crash-loop keys on *short-lived* instances so Chrome's 49 long-lived helpers are NOT flagged.

**Shape.**
```
  /api/incidents  ── incidents(db): one pass over cached events (per-pid cpu/rss stats) +
                     one pass over graph nodes (per-exe short-lived instance counts)
                  ── ranked, capped at 15, each {kind,title,detail,pid,exe,severity}
  UI: header "⚠ Incidents (N)" button ─▶ modal list of cards ─▶ click a card = focus that process
```

**UX / latency.** Fetched lazily (after first paint) and refreshed with the poll; the header badge
shows the count. One pass ≈ 0.3–0.5s on the cached events. Cards are click-to-focus.

## v1.2b — Timeline rewind  *(scrub to any past moment)*

**Goal.** The headline. Drag a timeline to 2:14pm and the whole UI shows the machine *then*.

**Backend.** Add an optional `as_of=<ns>` to `/api/graph`, `/api/proc`, `/api/incidents`. When set,
build/annotate from events with `ts ≤ as_of`, treat "current" = latest sample ≤ as_of, and "alive" =
sampled within grace of as_of. A `/api/window` returns `{min_ts, max_ts}` for the scrubber range.
`_load_asof(db, as_of)` filters the cached in-memory events and builds — cached by `as_of` so
repeated queries at one scrub position reuse the build.

**UX / latency (the make-or-break).**
- A slim **timeline bar** across the bottom, spanning the capture window, with a draggable handle
  and a live timestamp label.
- **Debounced**: dragging updates the label instantly (cheap, client-side time math); the data
  re-query fires ~200ms after the handle settles, not on every pixel — so one ~0.8s query per
  settle, with a subtle "as of HH:MM:SS" badge and a soft loading shimmer, never a frozen drag.
- While rewound, **auto-refresh pauses** and the header shows **"⏸ rewound — HH:MM:SS"** with a
  **"Live"** button to snap back to now. Snapping back re-enables the poll.
- Everything (list, roll-ups, summary, graph, inspector, incidents) honors `as_of`.

## v1.3a — Shareable incident report  *(the black box)*

**Goal.** Capture "what my machine was doing" as one self-contained file to hand to IT/a dev.

**Design.** Generated **client-side** (no server work): from the current view (respecting `as_of`),
build a standalone HTML string — machine-status summary, top processes (with mini CPU/mem numbers),
the incident list, the focused process's command + lineage, and an inline SVG of the current graph.
Everything inlined (no external refs, matches the offline guarantee). `Blob` → download
`causegraph-report-<stamp>.html`. Opens anywhere, no server.

**UX.** An **"Export"** button (header or footer). One click → a file downloads; a toast confirms.

## v1.3b — Alerts / watches

**Goal.** "Tell me when X happens" — a quiet recorder that speaks up only when it matters.

**Design (client-side, no backend, no persistence server-side).** A small rules list kept in
`localStorage`: each rule is `{metric: cpu|mem, op: ≥, value, forApp?}`. On each auto-refresh the UI
evaluates rules against the live roll-ups/processes; a newly-satisfied rule fires a **toast** and
lands in an **alerts log** (with de-dupe so it doesn't spam every 4s). Optional: ask for the browser
Notification permission to also pop a system notification.

**UX.** An **"Alerts"** section (in the incidents modal): add a rule in one line ("CPU ≥ 80%"),
see active rules and a small log of what fired. Deterministic, local, no noise.

---

## Acceptance criteria
- Incidents: a busy/looping/leaking process shows a correct plain-English card; clicking focuses it.
- Timeline: dragging the bar shows past state; "Live" returns to now; auto-refresh pauses while rewound.
- Report: Export downloads a self-contained HTML that opens offline and shows the summary + incidents + graph.
- Alerts: a rule fires a toast+log entry once when crossed, not repeatedly.
- No interaction feels frozen: first paint never blocks on the new work; scrub is debounced; each query ≤ ~0.8s.

## Test plan
| dimension | mechanism |
|---|---|
| correctness | unit tests for `incidents()` (cpu/leak/crashloop fixtures) and `as_of` filtering (state at T excludes later spawns); report is a pure string builder (assert it contains the key sections, no `http`) |
| latency | each new endpoint is one pass over cached events; timeline debounced; verified with timing on the live 1M-event DB |
| ui | headless-Chrome screenshots: incidents modal, rewound state + Live badge, exported report opened standalone, an alert firing |
| regression | existing `make test` green; offline guarantee (no external refs) preserved |

## Risks (accepted)
- Timeline scrub does one rebuild per settle (~0.8s) — acceptable with debounce + loading hint; a
  future snapshot index could make it instant. `[accepted]`
- Detector thresholds are heuristic; tuned conservatively to avoid false positives, refine from use. `[accepted]`
- Alerts live in the browser tab (localStorage) — they don't fire when the tab is closed; a daemon-side
  rule engine is a later step if needed. `[accepted]`
