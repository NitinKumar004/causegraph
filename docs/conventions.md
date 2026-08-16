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

Do not build a confidence scorer/precision filter until a rule actually emits a <1.0 edge; with one certain edge type there is nothing to score — defer, don't ship dead code. — causegraph-M4-M5
Keep the LLM/narrator a translator only: resolver picks the entry node, traversal reasons, narrator/llm phrase; the narrator emits ALL user-facing text (incl. caveats), the resolver prints none. — causegraph-M4-M5
Only treat 'pid N'/'process N' as an explicit pid in a free-text question; a bare number is not a pid. — causegraph-M4-M5
A `cg why` (or any query) fixture is separate from the tree/path fixture so adding resource.samples can't shift the ancestry goldens. — causegraph-M4-M5

Give every node-key type a single constructor that normalizes (e.g. model.file_key does normpath); two call sites feeding raw vs normalized forms of the same key silently desync edges from nodes. — causegraph-M3f-M4
When multiple collectors feed one channel, fan them in with a WaitGroup and close the channel exactly once after Wait() — never let each producer close it (double-close panic). — causegraph-M3f-M4
The first inferred (<1.0) edge sets the confidence contract: provenance = min of the edge's endpoints; precision comes from a structural match (path/exe), not from the confidence threshold. — causegraph-M3f-M4
A new event KIND is a builder change (builder owns kind→node); a new inferred RULE over existing node types is not (edge rules propose edges only). — causegraph-M3f-M4 (ADR 0001)

The web/API layer is pure presentation: graph_payload only serializes what the engine already computed — no reasoning in api/ or ui/. Bind the local server to 127.0.0.1 and fix the db at startup (never read a db path from the request). — causegraph-M6
Bound any graph→payload/render on ALL unbounded axes (descendants AND fan-out edges), not just the obvious one, with an explicit truncated flag. — causegraph-M6
A fixture-build step must rebuild clean (rm first) because cg load appends; a golden recorded from an accumulated DB is silently wrong. — causegraph-M6
