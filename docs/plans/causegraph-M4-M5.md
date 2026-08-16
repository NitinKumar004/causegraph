# causegraph-M4-M5 — the "why is X hot?" vertical (offline)

> Contract. Builds on M0–M2. Ships the parts of M4/M5 the **currently-captured data supports**:
> resource attribution + M5-offline query (`cg why`). Scoring, inferred edges and precision
> traversal are DEFERRED — with only process+resource data there is no confidence<1.0 edge to
> score or filter; they land in a true M4 alongside M3's file/socket capture. Faithful to
> `docs/architecture.md` §5.3–5.4; divergences tagged `[A]`.

## Goal

`cg why "<question>"` answers a plain question — "why is the fan loud?", "what's using memory?"
— by resolving it to the culprit process (ranked by captured CPU/RSS), walking its ancestry,
and printing a deterministic plain-English explanation, entirely offline (no API key, no network).

## Out of scope (and why)

- **Scoring (`scoring.py`), inferred edges, precision (min-confidence) traversal** — DEFERRED:
  no rule in scope emits a <1.0 edge (file_watch/socket need M3 data; the certain ppid edge is
  1.0). Building a confidence filter with nothing to filter would be dead code. Lands in M4-full.
- Real LLM provider (`llm.py` ships an offline `LocalTemplateNarrator` default; provider slot only).
- `file_watch`/`socket`/`cron` edge rules (no file/socket/scheduler data yet — needs M3).
- Native backends (M3), web UI (M6), fleet. Real temperature (no sensor → CPU is the proxy).

## Shape

```text
                        +--> builder[R] --------> nx.DiGraph (ppid edges, conf 1.0)
 events (SQLite)        |                              |
   --reader[R]--> evs --+                              v
   (ONE read)           +--> attribution.py * --annotate(g, evs)--> nodes get peak_cpu/rss
                             (matched by instance window;   |         (evs, NOT the graph, is
                              O(resource.samples))          v          the metric source)
  cg why "<q>" *                                  rank_by(g, metric) *
     |  question          +-----------+  metric        ^
     v                    | resolver *|----------------+
  resolver * ------------>| kw->intent|  entry node
     |                    +-----------+       |
     |  (pid? honored, bypasses rank_by)      v
     +-----> traverse.ancestry_path [R] ---path---> narrator.py * explain(path,culprit,metric,value)
                                                        ^                     |
                                          llm.py * get_narrator() ------------+  text
                                          (registry; Narrator Protocol lives here;
                                           default LocalTemplateNarrator in narrator.py)
```
The CLI reads events ONCE and hands the same list to both builder and attribution — no re-read.

Reasoning stays in the graph + resolver + traversal (deterministic). narrator/llm only phrase —
"translator, never reasoner" (§5.4). llm.py does zero re-derivation of the sentence.

## Precedent

- `[R]` reader / builder / traversal (`ancestry_path`, `latest_instance`, `parent_of`) / CLI
  subcommand pattern / schema metrics — see `.evidence/precedent.md`.
- `[N]` `graph/attribution.py`, `query/{resolver,narrator,llm}.py` — none exist.
- `[A]` resource attribution is **node annotation** (peak metric per node) used to pick the entry
  node — NOT the doc's `edges/resource.py` EdgeRule (§4 tree, §5.3) that would attribute a spike to
  another process — reversible: a later EdgeRule can consume the same peak data to propose an edge.
- `[A]` narrator deterministic; `llm.py` offline default — user chose no LLM provider — reversible:
  register a provider in `llm.py`.
- `[A]` "fan/hot/heat" → sustained high **CPU** (no temp sensor: `daemon/internal/collector/generic/
  diff.go:73-80` `resourceSample` builds `Metrics{CPUPct, RSSBytes}` only, never `TempC`) — reversible:
  sensor/native backend.
- `[A]` resolver is keyword/intent based, not NLU — sufficient offline; llm slot upgrades later.

## Unknowns → spikes

- none — extends proven seams on data we already capture (process.spawn + resource.sample).

## Acceptance criteria

1. `attribution.annotate(g, events)` takes the SAME event list the builder consumed (single read,
   no re-scan of the DB) and matches each `resource.sample` to the node **instance** whose
   `[spawn_ts, exit_ts)` window contains the sample ts (reusing builder `by_pid` ordering, not pid
   alone — pid-reuse safe); annotates `peak_cpu_pct` / `peak_rss_bytes` per node.
2. `rank_by(g, metric)` returns nodes ordered desc by that peak metric; nodes with no sample get
   `peak_*=None` and sort **last**, never raising `TypeError`.
3. Resolver maps intent deterministically: fan/hot/heat/loud/cpu/slow/busy → hottest-CPU node;
   memory/ram/leak → hottest-RSS node; an explicit pid in the question is honored; an explicit pid
   absent from the graph → clear message + exit 1 (matching `cg path`). An **empty** question
   (after trim) → clear message + exit 1. A **non-empty question matching no keyword** → falls back
   to hottest-CPU and calls the narrator with `assumed=True` (the caveat text is emitted by the
   narrator, not the resolver), exit 0. Never a crash;
   question length capped at 4096 (longer → exit 1).
4. `narrator.py` owns ALL user-facing text: `explain(path, culprit, metric, value, assumed=False)
   -> str`, deterministic (identical output for identical input), names culprit exe/pid/metric-value
   + the ancestry chain, states heat is inferred from CPU, and — when `assumed=True` — emits the
   keyword-miss "assuming CPU" caveat itself (the resolver prints NO text of its own). No network.
5. `llm.py` owns the `Narrator` Protocol (`explain(path, culprit, metric, value, assumed=False) -> str`,
   so both modules import it without a cycle) and a pure registry `get_narrator(name=None) -> Narrator`;
   default returns `narrator.LocalTemplateNarrator`
   (which implements the Protocol structurally, no back-import, does zero re-derivation); an unknown
   provider name raises a clear error; no provider makes a network call.
6. `cg why "<question>" --db <fixture>` prints the deterministic explanation, exit 0. If the chosen
   culprit's `peak_<metric>` is `None` — whether from `rank_by`'s top result (empty DB / zero
   resource.samples) OR from the explicit-pid branch resolving to a node with no attribution data
   (that branch bypasses `rank_by`) — the resolver takes the exit-1 "no attribution data" path and
   never passes a None value to `narrator.py`.
7. All M0–M2 behavior unchanged; `make test` green including the new tests.

## Test plan

| Dimension | Proven by | Gate |
|---|---|---|
| correctness | table tests: attribution instance-window matching + pid-reuse + None-safe rank; resolver keyword→metric + explicit-pid + absent-pid + empty; narrator golden string; llm registry default + unknown-provider error | must pass |
| reliability | resolver on empty DB and on a DB with zero resource.samples → clear message, exit 1, no traceback; narrator on a single-node path (no ancestry); 4096-char question accepted, longer rejected | must pass |
| concurrency | n/a — engine read-only, one graph per invocation, no shared mutable state (explicit) | n/a |
| scale | extend `scripts/scale_query.py`: fixture includes resource.samples (state the sample count); run N=20 repeated `attribution.annotate(g, events)` passes over ALL samples (the real O(samples) work) + resolver rank_by; report p50/p95/p99 added latency + peak-RSS delta vs the M2 baseline (7.6s/2.3 GiB, `.evidence/artifacts/scale_query.json`) | report |
| security | `cg why` free-text is tokenized in-process only — never in SQL (parameterized reads) or a shell; length capped 4096; no eval; pid still int-validated | must pass |
| regression | existing `cg tree`/`cg path` golden byte-identical; the 24 M0–M2 tests stay green; new `cg why` golden via the `cli` collector | must pass |

## Must not regress

- M0–M2 ancestry: `cg tree`/`cg path` output byte-identical (existing `.evidence/baseline`). The
  existing `test/fixtures/graph_events.jsonl` is NOT modified; `cg why` uses a SEPARATE fixture
  `test/fixtures/why_events.jsonl` (with resource.samples) so tree/path goldens are untouched.
- The EdgeRule seam stays additive — `test_builder.py::test_rule_sees_raw_events_incl_metrics` passes.

## Evidence plan

- `cli` collector — add `cg why "fan is loud"` + `cg why "what is using memory"` golden cases
  against the NEW `test/fixtures/why_events.jsonl` (leaving the tree/path fixture untouched).
- `.evidence/artifacts/` — resolver/attribution added-cost (p50/p95/p99 + RSS) at 1M rows.

## Rollback

- How: purely additive — new files (`attribution.py`, `query/*`), one `cg why` subcommand, an
  extended fixture. Revert the commits. No schema change, no data migration, no daemon change.
- Migration reversible: yes — n/a (no store/schema change).

## Open risks I'm accepting

- Fan/heat inferred from sustained CPU, not temperature (no sensor) — the narrator says so explicitly.
- Resolver is keyword-based; an off-vocabulary question falls back to hottest-CPU with a stated
  assumption — acceptable offline; `llm.py` is where real NLU lands later.
- Scoring/inferred-edges/precision-traversal deferred to M4-full (needs M3 data) — `[unresolved-review]`
  turned into a deliberate descope, not a silent gap.
