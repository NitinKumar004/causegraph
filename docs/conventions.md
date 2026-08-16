# Conventions

Rules learned from the edits you made to plans the harness proposed. Written by
`/ship-harness:ship` at S7, one line per rule.

Format: `When X, do Y (not Z). — <ticket>`

Hard cap: 40 lines. Merge duplicates. Delete anything you have overridden twice —
a rule you keep breaking was never a rule.

When adding a scale/benchmark, drive the real path (pipeline.Ingest/Run), not a direct store call, and report MEASURED drop counters (never a hardcoded 0). — causegraph-M0-M2
When shipping an M-scoped slice of a larger system, prove each named seam with a test that a later milestone's shape would exercise (e.g. an EdgeRule reading raw events/metrics). — causegraph-M0-M2
Every acceptance criterion, including the daemon entrypoint, gets a test; extract main() into a testable run(ctx,cfg) so the full lifecycle (heartbeat, clean SIGINT shutdown) is covered. — causegraph-M0-M2
Cross-language artifacts (event schema AND the store table DDL) each need a drift gate on both the Go and Python sides. — causegraph-M0-M2
Generated code is committed and gofmt'd by the generator; the drift gate is `gen.py --check` (in-memory byte compare). — causegraph-M0-M2
